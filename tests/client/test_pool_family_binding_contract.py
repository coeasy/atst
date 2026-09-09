from __future__ import annotations

import pytest

from tstdx.client import AsyncF10Client, AsyncTdxClient, F10Client, TdxClient
from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def _host(family: str) -> HostEntry:
    return HostEntry(host="127.0.0.1", port=7709, family=family)


def test_sync_client_rejects_real_pool_from_another_family() -> None:
    pool = ConnectionPool(
        [_host(Family.STANDARD)],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        with pytest.raises(ConfigError, match="client/pool family 不匹配"):
            F10Client(pool=pool)
    finally:
        pool.close()


def test_async_client_rejects_real_pool_from_another_family() -> None:
    pool = AsyncConnectionPool(
        [_host(Family.STANDARD)],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )

    with pytest.raises(ConfigError, match="client/pool family 不匹配"):
        AsyncF10Client(pool=pool)


def test_matching_real_pool_family_is_accepted_sync_and_async() -> None:
    sync_pool = ConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    async_pool = AsyncConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        sync_client = F10Client(pool=sync_pool)
        async_client = AsyncF10Client(pool=async_pool)
        assert sync_client.family == Family.F10
        assert async_client.family == Family.F10
    finally:
        sync_pool.close()


def test_opaque_legacy_pool_double_remains_supported() -> None:
    class OpaquePool:
        pass

    sync_client = TdxClient(pool=OpaquePool())
    async_client = AsyncTdxClient(pool=OpaquePool())

    assert sync_client.family == Family.STANDARD
    assert async_client.family == Family.STANDARD


def test_declared_none_pool_family_is_not_treated_as_opaque() -> None:
    class InvalidPool:
        family = None

    with pytest.raises(ConfigError, match="client/pool family 不匹配"):
        TdxClient(pool=InvalidPool())
    with pytest.raises(ConfigError, match="client/pool family 不匹配"):
        AsyncTdxClient(pool=InvalidPool())
