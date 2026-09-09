from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def test_update_hosts_publishes_fresh_generation_with_old_identity_and_new_latency() -> None:
    current = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="verified-primary",
        verified=True,
        connect_ms=20.0,
        rtt_ms=30.0,
        live_rtt_ms=40.0,
        live_ok_at=122.0,
        failures=4,
        biz_failures=2,
        last_ok=123.0,
        last_error="ConnectionFailed: timeout",
        circuit="open",
        consec_weighted=8.0,
        circuit_opened_at=456.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]
    observed = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="probe-must-not-win",
        verified=False,
        connect_ms=2.0,
        rtt_ms=3.0,
        failures=0,
        last_ok=999.0,
        circuit="healthy",
    )

    updated = pool.update_hosts([observed])
    published = updated[0]

    assert published is not current
    assert pool.hosts[0] is published
    assert pool._slots[0].host is published
    # The idle connection slot is reusable, but its generation and HostEntry are new.
    assert pool._slots[0] is old_slot
    assert pool._slots[0].generation == 1

    assert published.connect_ms == 2.0
    assert published.rtt_ms == 3.0
    assert published.name == "verified-primary"
    assert published.verified is True
    assert published.live_rtt_ms == 40.0
    assert published.live_ok_at == 122.0
    assert published.failures == 4
    assert published.biz_failures == 2
    assert published.last_ok == 123.0
    assert published.last_error == "ConnectionFailed: timeout"
    assert published.circuit == "open"
    assert published.consec_weighted == 8.0
    assert published.circuit_opened_at == 456.0

    # External references to the retired generation never share mutable health.
    assert current.connect_ms == 20.0
    assert current.rtt_ms == 30.0
    assert current.live_rtt_ms == 40.0


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

    published = pool.update_hosts([failed_probe])[0]

    assert published is not current
    assert published.rtt_ms == 5.0
    assert published.failures == 3
    assert published.circuit == "degraded"
    assert published.last_error == "request failed"
    assert current.rtt_ms == 5.0
    assert current.failures == 3


def test_update_hosts_half_open_token_is_not_carried_into_new_generation() -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=5.0,
        circuit="half_open",
        circuit_probe_inflight=True,
        circuit_opened_at=1.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)

    published = pool.update_hosts(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD, rtt_ms=2.0)]
    )[0]

    assert published.circuit == "open"
    assert published.circuit_probe_inflight is False
    assert published.circuit_opened_at > 1.0
    assert current.circuit == "half_open"
    assert current.circuit_probe_inflight is True


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
