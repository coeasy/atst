from __future__ import annotations

import asyncio
from typing import Any

import pytest

from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry


def _pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        [HostEntry("127.0.0.1", 7709)],
        slots_per_host=1,
        heartbeat_interval=0,
        handshake=False,
    )


def test_cancel_before_close_commit_leaves_old_generation_untouched() -> None:
    async def run() -> None:
        pool = _pool()
        slot = pool._slots[0]
        generation = pool._generation
        await slot.lock.acquire()
        try:
            close_task = asyncio.create_task(pool.close())
            for _ in range(32):
                await asyncio.sleep(0)
                if pool._lock.locked():
                    break
            assert pool._lock.locked()
            assert pool._closed is False
            assert slot.retired is False
            assert pool._generation == generation

            close_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await close_task

            assert pool._closed is False
            assert slot.retired is False
            assert pool._generation == generation
        finally:
            if slot.lock.locked():
                slot.lock.release()
            await pool.close()

    asyncio.run(run())


def test_cancel_after_close_commit_waits_for_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = _pool()
        slot = pool._slots[0]
        entered = asyncio.Event()
        release = asyncio.Event()
        cleaned = asyncio.Event()

        async def blocked_drop(_slot: Any, *, expected: Any = None) -> None:
            del expected
            assert _slot is slot
            entered.set()
            await release.wait()
            cleaned.set()

        monkeypatch.setattr(pool, "_drop", blocked_drop)

        close_task = asyncio.create_task(pool.close())
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        assert pool._closed is True
        assert slot.retired is True
        assert pool._hb is None

        close_task.cancel()
        await asyncio.sleep(0)
        assert close_task.done() is False
        assert cleaned.is_set() is False

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await close_task
        assert cleaned.is_set() is True

    asyncio.run(run())


def test_close_drains_remaining_slots_before_reporting_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = AsyncConnectionPool(
            [HostEntry("127.0.0.1", 7709), HostEntry("127.0.0.2", 7709)],
            slots_per_host=1,
            heartbeat_interval=0,
            handshake=False,
        )
        first, second = pool._slots
        dropped: list[Any] = []

        async def fail_first_drop(slot: Any, *, expected: Any = None) -> None:
            del expected
            dropped.append(slot)
            if slot is first:
                raise RuntimeError("first cleanup failed")

        monkeypatch.setattr(pool, "_drop", fail_first_drop)
        with pytest.raises(RuntimeError, match="first cleanup failed"):
            await pool.close()

        assert dropped == [first, second]
        assert pool._closed is True
        assert first.retired is True
        assert second.retired is True

    asyncio.run(run())


def test_repeated_close_redrains_already_closed_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = _pool()
        slot = pool._slots[0]
        pool._closed = True
        generation = pool._generation
        slot.retired = False
        dropped: list[Any] = []

        async def capture_drop(_slot: Any, *, expected: Any = None) -> None:
            del expected
            dropped.append(_slot)

        monkeypatch.setattr(pool, "_drop", capture_drop)
        await pool.close()

        assert dropped == [slot]
        assert slot.retired is True
        assert pool._closed is True
        assert pool._generation == generation

    asyncio.run(run())


def test_async_close_public_wiring_uses_atomic_hardening() -> None:
    assert AsyncConnectionPool.close.__module__ == "atst.transport.async_"
