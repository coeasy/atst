from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def test_update_hosts_only_overlays_latency_for_existing_endpoint() -> None:
    current = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="verified-primary",
        verified=True,
        connect_ms=20.0,
        rtt_ms=30.0,
        failures=4,
        biz_failures=2,
        last_ok=123.0,
        last_error="ConnectionFailed: timeout",
        circuit="open",
        consec_weighted=8.0,
        circuit_opened_at=456.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    observed = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        connect_ms=2.0,
        rtt_ms=3.0,
        failures=0,
        last_ok=999.0,
        circuit="healthy",
    )

    updated = pool.update_hosts([observed])

    assert updated == [current]
    assert updated[0] is current
    assert pool.hosts[0] is current
    assert pool._slots[0].host is current
    assert current.connect_ms == 2.0
    assert current.rtt_ms == 3.0
    assert current.name == "verified-primary"
    assert current.verified is True
    assert current.failures == 4
    assert current.biz_failures == 2
    assert current.last_ok == 123.0
    assert current.last_error == "ConnectionFailed: timeout"
    assert current.circuit == "open"
    assert current.consec_weighted == 8.0
    assert current.circuit_opened_at == 456.0


def test_update_hosts_failed_probe_does_not_erase_existing_latency_or_health() -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=5.0,
        failures=3,
        circuit="degraded",
        last_error="request failed",
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    failed_probe = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=None,
        failures=1,
        circuit="healthy",
    )

    pool.update_hosts([failed_probe])

    assert current.rtt_ms == 5.0
    assert current.failures == 3
    assert current.circuit == "degraded"
    assert current.last_error == "request failed"


def test_update_hosts_rejects_cross_family_or_duplicate_provenance() -> None:
    pool = ConnectionPool(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD)],
        slots_per_host=1,
        heartbeat_interval=0,
    )

    with pytest.raises(ConfigError, match="family 不匹配"):
        pool.update_hosts([HostEntry(host="1.2.3.4", family=Family.F10)])

    duplicate = HostEntry(host="1.2.3.4", family=Family.STANDARD)
    with pytest.raises(ConfigError, match="重复 endpoint"):
        pool.update_hosts([duplicate, duplicate])
