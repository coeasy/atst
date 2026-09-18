from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def test_update_hosts_publishes_fresh_generation_with_old_identity_and_new_latency(
    seed_pool_health,
) -> None:
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
    # The constructed pool owns a fresh runtime-health generation, so live
    # request health is re-established here exactly as the pool's own request
    # path would record it.
    seed_pool_health(
        pool,
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


def test_update_hosts_canonicalizes_identity_before_generation_matching(
    seed_pool_health,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="canonical-primary",
        verified=True,
        live_rtt_ms=9.0,
        live_ok_at=10.0,
        failures=2,
        circuit="degraded",
        consec_weighted=3.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]
    seed_pool_health(
        pool,
        live_rtt_ms=9.0,
        live_ok_at=10.0,
        failures=2,
        circuit="degraded",
        consec_weighted=3.0,
    )

    published = pool.update_hosts(
        [
            HostEntry(
                host=" 1.2.3.4 ",
                port=7709,
                family=Family.STANDARD,
                rtt_ms=2.0,
            )
        ]
    )[0]

    assert published.host == "1.2.3.4"
    assert published.name == "canonical-primary"
    assert published.verified is True
    assert published.live_rtt_ms == 9.0
    assert published.live_ok_at == 10.0
    assert published.failures == 2
    assert published.circuit == "degraded"
    assert published.consec_weighted == 3.0
    assert published.rtt_ms == 2.0
    assert pool._slots[0] is old_slot
    assert pool._slots[0].generation == 1


def test_update_hosts_failed_probe_invalidates_probe_latency_but_preserves_live_health(
    seed_pool_health,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        connect_ms=4.0,
        rtt_ms=5.0,
        live_rtt_ms=7.0,
        live_ok_at=8.0,
        failures=3,
        circuit="degraded",
        last_error="request failed",
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    seed_pool_health(
        pool,
        live_rtt_ms=7.0,
        live_ok_at=8.0,
        failures=3,
        circuit="degraded",
        last_error="request failed",
    )
    failed_probe = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=None,
        failures=1,
        last_error="probe failed",
        circuit="healthy",
    )

    published = pool.update_hosts([failed_probe])[0]

    assert published is not current
    assert published.connect_ms is None
    assert published.rtt_ms is None
    assert published.live_rtt_ms == 7.0
    assert published.live_ok_at == 8.0
    assert published.score == 7.0 * (4**3)
    assert published.failures == 3
    assert published.circuit == "degraded"
    assert published.last_error == "request failed"
    # The previous generation remains untouched for in-flight/external owners.
    assert current.connect_ms == 4.0
    assert current.rtt_ms == 5.0
    assert current.live_rtt_ms == 7.0
    assert current.failures == 3


def test_update_hosts_failed_probe_without_live_health_demotes_stale_probe() -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=1.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)

    published = pool.update_hosts(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                failures=1,
                last_error="probe failed",
            )
        ]
    )[0]

    assert published.rtt_ms is None
    assert published.live_rtt_ms is None
    assert published.score == 1_000_000.0


def test_update_hosts_half_open_token_is_not_carried_into_new_generation(
    seed_pool_health,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=5.0,
        circuit="half_open",
        circuit_probe_inflight=True,
        circuit_opened_at=1.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    seed_pool_health(
        pool,
        circuit="half_open",
        circuit_probe_inflight=True,
        circuit_opened_at=1.0,
    )

    published = pool.update_hosts([HostEntry(host="1.2.3.4", family=Family.STANDARD, rtt_ms=2.0)])[
        0
    ]

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
