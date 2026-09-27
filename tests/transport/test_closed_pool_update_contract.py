from __future__ import annotations

import asyncio

import pytest

from atst.errors import ConnectionClosed
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool


def _host(address: str = "127.0.0.1") -> HostEntry:
    return HostEntry(address, 7709)


def test_sync_closed_pool_rejects_empty_and_nonempty_host_updates() -> None:
    pool = ConnectionPool([_host()], slots_per_host=1, heartbeat_interval=0)
    pool.close()
    generation = pool._generation
    hosts = list(pool.hosts)
    slots = list(pool._slots)

    with pytest.raises(ConnectionClosed):
        pool.update_hosts([])
    with pytest.raises(ConnectionClosed):
        pool.update_hosts([_host("127.0.0.2")])

    assert pool._generation == generation
    assert pool.hosts == hosts
    assert pool._slots == slots


def test_async_closed_pool_rejects_empty_and_nonempty_host_updates() -> None:
    async def run() -> None:
        pool = AsyncConnectionPool([_host()], slots_per_host=1, heartbeat_interval=0)
        await pool.close()
        generation = pool._generation
        hosts = list(pool.hosts)
        slots = list(pool._slots)

        with pytest.raises(ConnectionClosed):
            await pool.update_hosts([])
        with pytest.raises(ConnectionClosed):
            await pool.update_hosts([_host("127.0.0.2")])

        assert pool._generation == generation
        assert pool.hosts == hosts
        assert pool._slots == slots

    asyncio.run(run())
