from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import tstdx.transport._pool_provenance_hardening as hardening
import tstdx.transport.speedtest as speedtest_module
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool
from tstdx.transport.speedtest import ProbeResult


@pytest.mark.asyncio
async def test_async_update_hosts_publishes_fresh_generation_with_old_identity() -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        name="verified-primary",
        verified=True,
        rtt_ms=30.0,
        live_rtt_ms=40.0,
        failures=3,
        last_error="current failure",
        circuit="degraded",
        consec_weighted=3.0,
    )
    pool = AsyncConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]

    published = (
        await pool.update_hosts(
            [
                HostEntry(
                    host="1.2.3.4",
                    family=Family.STANDARD,
                    name="probe-name",
                    verified=False,
                    rtt_ms=2.0,
                    failures=0,
                    circuit="healthy",
                )
            ]
        )
    )[0]

    assert published is not current
    assert pool.hosts[0] is published
    assert pool._slots[0] is old_slot
    assert pool._slots[0].host is published
    assert pool._slots[0].generation == 1
    assert published.name == "verified-primary"
    assert published.verified is True
    assert published.rtt_ms == 2.0
    assert published.live_rtt_ms == 40.0
    assert published.failures == 3
    assert published.last_error == "current failure"
    assert published.circuit == "degraded"
    assert published.consec_weighted == 3.0
    assert current.rtt_ms == 30.0


@pytest.mark.asyncio
async def test_async_update_hosts_rejects_cross_family_and_duplicates() -> None:
    pool = AsyncConnectionPool(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD)],
        slots_per_host=1,
        heartbeat_interval=0,
    )

    with pytest.raises(Exception, match="family 不匹配"):
        await pool.update_hosts([HostEntry(host="1.2.3.4", family=Family.F10)])

    duplicate = HostEntry(host="1.2.3.4", family=Family.STANDARD)
    with pytest.raises(Exception, match="重复 endpoint"):
        await pool.update_hosts([duplicate, duplicate])


def test_stale_background_probe_cannot_commit_after_generation_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=50.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    captured: list[Callable[[], None]] = []

    class DeferredThread:
        def __init__(
            self,
            *,
            target: Callable[[], None],
            name: str,
            daemon: bool,
        ) -> None:
            assert name == "tstdx-speedtest"
            assert daemon is True
            self._target = target

        def start(self) -> None:
            captured.append(self._target)

    monkeypatch.setattr(hardening.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        speedtest_module,
        "speedtest",
        lambda *_args, **_kwargs: [
            ProbeResult(
                host="1.2.3.4",
                port=7709,
                family=Family.STANDARD,
                ok=True,
                connect_ms=1.0,
                rtt_ms=2.0,
            )
        ],
    )

    class ExplodingStore:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("stale generation must not open persistent ranking")

    monkeypatch.setattr(hardening, "RankingStore", ExplodingStore)

    pool._trigger_background_speedtest()
    assert pool._speedtest_triggered is True
    assert len(captured) == 1

    published = pool.update_hosts(
        [HostEntry(host="5.6.7.8", family=Family.STANDARD, rtt_ms=10.0)]
    )[0]
    assert pool._generation == 1
    assert pool._speedtest_triggered is False

    # Execute the old worker only after the new generation is already public.
    captured.pop()()

    assert published.host == "5.6.7.8"
    assert published.rtt_ms == 10.0
    assert current.rtt_ms == 50.0

    # New-generation failures remain able to schedule a fresh background probe.
    pool._trigger_background_speedtest()
    assert pool._speedtest_triggered is True
    assert len(captured) == 1
