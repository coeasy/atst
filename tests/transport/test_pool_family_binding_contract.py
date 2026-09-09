from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def _host(family: str) -> HostEntry:
    return HostEntry(host="127.0.0.1", port=7709, family=family)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_unknown_family(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="family 非法"):
        pool_cls([_host(Family.STANDARD)], family="unknown", heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_host_from_another_family(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="host family 不匹配"):
        pool_cls([_host(Family.F10)], family=Family.STANDARD, heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_non_hostentry_sequence(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="只接受 HostEntry"):
        pool_cls([object()], family=Family.STANDARD, heartbeat_interval=0)


def test_sync_async_constructor_signatures_survive_hardening_wrapper() -> None:
    sync = inspect.signature(ConnectionPool.__init__)
    async_ = inspect.signature(AsyncConnectionPool.__init__)

    assert "hosts" in sync.parameters
    assert "family" in sync.parameters
    assert "hosts" in async_.parameters
    assert "family" in async_.parameters
    assert ConnectionPool.__init__.__module__ == "tstdx.transport._pool_family_hardening"
    assert AsyncConnectionPool.__init__.__module__ == "tstdx.transport._pool_family_hardening"


def test_valid_same_family_pool_still_constructs() -> None:
    sync = ConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    async_ = AsyncConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        assert sync.family == Family.F10
        assert async_.family == Family.F10
    finally:
        sync.close()
        asyncio.run(async_.close())
