from __future__ import annotations

import asyncio
import importlib
import threading

import pytest

import tstdx.transport.hosts as hosts_module
from tstdx.client import AsyncTdxClient
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.speedtest import ProbeResult

speedtest_module = importlib.import_module("tstdx.transport.speedtest")


@pytest.mark.asyncio
async def test_cancelled_async_bestip_cannot_commit_pool_or_persistent_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=40.0,
        live_rtt_ms=7.0,
        failures=2,
        circuit="degraded",
    )
    pool = AsyncConnectionPool(
        [original],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    client = AsyncTdxClient(pool=pool)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    ranking_updates: list[list[HostEntry]] = []

    def blocking_speedtest(hosts, **kwargs):
        del kwargs
        assert hosts[0] is not original
        started.set()
        try:
            assert release.wait(timeout=5.0), "test did not release blocked speedtest"
            hosts[0].rtt_ms = 1.0
            hosts[0].live_rtt_ms = 999.0
            return [
                ProbeResult(
                    host=hosts[0].host,
                    port=hosts[0].port,
                    family=Family.STANDARD,
                    ok=True,
                    rtt_ms=1.0,
                )
            ]
        finally:
            finished.set()

    class RecordingStore:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def update(self, entries) -> None:
            ranking_updates.append(list(entries))

    monkeypatch.setattr(speedtest_module, "speedtest", blocking_speedtest)
    monkeypatch.setattr(hosts_module, "RankingStore", RecordingStore)

    task = asyncio.create_task(client.bestip(save_ranking=True))
    try:
        for _ in range(200):
            if started.is_set():
                break
            await asyncio.sleep(0)
        assert started.is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Let the executor thread finish after cancellation. Its detached host
        # may change, but it has no live-pool or RankingStore commit authority.
        release.set()

        async def wait_finished() -> None:
            while not finished.is_set():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_finished(), timeout=2.0)

        assert pool._generation == 0
        assert pool.hosts == [original]
        assert pool.hosts[0] is original
        assert original.rtt_ms == 40.0
        assert original.live_rtt_ms == 7.0
        assert original.failures == 2
        assert original.circuit == "degraded"
        assert ranking_updates == []
    finally:
        release.set()
        await pool.close()
