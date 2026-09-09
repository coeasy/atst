from __future__ import annotations

import asyncio
import importlib
import threading
from typing import Any

import pytest

from tstdx.client import AsyncTdxClient
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry
from tstdx.transport.speedtest import ProbeResult

speedtest_mod = importlib.import_module("tstdx.transport.speedtest")
hosts_mod = importlib.import_module("tstdx.transport.hosts")


def test_cancelled_async_bestip_cannot_commit_after_probe_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    pool_updates: list[list[HostEntry]] = []
    ranking_writes: list[list[HostEntry]] = []

    def blocking_speedtest(*args: Any, **kwargs: Any) -> list[ProbeResult]:
        del args, kwargs
        started.set()
        try:
            assert release.wait(timeout=2.0)
            return [
                ProbeResult(
                    host="127.0.0.1",
                    port=7709,
                    family=Family.STANDARD,
                    ok=True,
                    rtt_ms=1.0,
                )
            ]
        finally:
            finished.set()

    class FakeStore:
        def update(self, entries: list[HostEntry]) -> None:
            ranking_writes.append(list(entries))

    class FakeAsyncPool:
        family = Family.STANDARD

        def __init__(self) -> None:
            self.hosts = [
                HostEntry(
                    host="127.0.0.1",
                    port=7709,
                    family=Family.STANDARD,
                )
            ]

        async def update_hosts(self, entries: list[HostEntry]) -> list[HostEntry]:
            copied = list(entries)
            pool_updates.append(copied)
            self.hosts = copied
            return copied

    monkeypatch.setattr(speedtest_mod, "speedtest", blocking_speedtest)
    monkeypatch.setattr(hosts_mod, "RankingStore", FakeStore)

    async def run() -> None:
        client = AsyncTdxClient(pool=FakeAsyncPool())
        task = asyncio.create_task(client.bestip(save_ranking=True))
        assert await asyncio.to_thread(started.wait, 1.0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # A cancelled run_in_executor Future does not stop its worker thread.
        # Let that worker complete, then prove no late continuation committed.
        release.set()
        assert await asyncio.to_thread(finished.wait, 1.0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert pool_updates == []
    assert ranking_writes == []
