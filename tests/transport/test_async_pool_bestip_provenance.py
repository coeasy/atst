from __future__ import annotations

import asyncio

import pytest

from tstdx.protocol.commands import Family
from tstdx.transport import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry


@pytest.mark.asyncio
async def test_async_update_hosts_canonicalizes_identity_before_generation_matching() -> None:
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
    pool = AsyncConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]
    try:
        published = (
            await pool.update_hosts(
                [
                    HostEntry(
                        host=" 1.2.3.4 ",
                        port=7709,
                        family=Family.STANDARD,
                        rtt_ms=2.0,
                    )
                ]
            )
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
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_async_update_hosts_cancelled_during_prepare_leaves_old_generation_untouched() -> None:
    first = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=10.0,
        live_rtt_ms=8.0,
        failures=2,
        circuit="degraded",
    )
    second = HostEntry(
        host="2.3.4.5",
        family=Family.STANDARD,
        rtt_ms=20.0,
        live_rtt_ms=18.0,
    )
    pool = AsyncConnectionPool(
        [first, second],
        slots_per_host=1,
        heartbeat_interval=0,
    )
    old_hosts = tuple(pool.hosts)
    old_slots = tuple(pool._slots)
    old_generation = pool._generation
    blocker = old_slots[1].lock
    await blocker.acquire()
    task = asyncio.create_task(
        pool.update_hosts(
            [
                HostEntry(host="1.2.3.4", family=Family.STANDARD, rtt_ms=1.0),
                HostEntry(host="2.3.4.5", family=Family.STANDARD, rtt_ms=2.0),
            ]
        )
    )

    try:
        # The update task acquires slot 0 and then blocks on slot 1, which this
        # test owns. At that point the preparation phase is definitely active.
        for _ in range(100):
            if old_slots[0].lock.locked():
                break
            await asyncio.sleep(0)
        assert old_slots[0].lock.locked()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert pool._generation == old_generation
        assert tuple(pool.hosts) == old_hosts
        assert tuple(pool._slots) == old_slots
        assert all(pool.hosts[index] is old_hosts[index] for index in range(2))
        assert all(pool._slots[index] is old_slots[index] for index in range(2))
        assert old_slots[0].generation == 0
        assert old_slots[1].generation == 0
        assert old_slots[0].retired is False
        assert old_slots[1].retired is False
        assert first.rtt_ms == 10.0
        assert first.live_rtt_ms == 8.0
        assert first.failures == 2
        assert first.circuit == "degraded"
        assert old_slots[0].lock.locked() is False
        assert blocker.locked() is True
    finally:
        if blocker.locked():
            blocker.release()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await pool.close()
