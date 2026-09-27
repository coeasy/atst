from __future__ import annotations

import asyncio
from typing import Any

import pytest

from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry


def _host(address: str) -> HostEntry:
    return HostEntry(address, 7709)


def test_cancel_after_async_host_commit_waits_for_retired_slot_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = AsyncConnectionPool(
            [_host("127.0.0.1"), _host("127.0.0.2")],
            slots_per_host=1,
            heartbeat_interval=0,
            handshake=False,
        )
        old_first = pool._slots[0]
        entered = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()

        async def blocked_drop(slot: Any, *, expected: Any = None) -> None:
            del expected
            assert slot is old_first
            entered.set()
            await release.wait()
            cleaned.set()

        monkeypatch.setattr(pool, "_drop", blocked_drop)

        task = asyncio.create_task(pool.update_hosts([_host("127.0.0.2")]))
        await asyncio.wait_for(entered.wait(), timeout=1.0)

        # Publication is already atomic and complete before cleanup starts.
        assert pool._generation == 1
        assert [host.host for host in pool.hosts] == ["127.0.0.2"]
        assert old_first.retired is True
        assert old_first in pool._retired_slots

        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        assert cleaned.is_set() is False

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set() is True

        # Restore a no-op drop so final close does not re-enter the test gate.
        async def final_drop(slot: Any, *, expected: Any = None) -> None:
            del slot, expected

        monkeypatch.setattr(pool, "_drop", final_drop)
        await pool.close()

    asyncio.run(run())
