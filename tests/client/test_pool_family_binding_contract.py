from __future__ import annotations

import asyncio
from typing import Any

import pytest

from atst.client import (
    AsyncExMarketClient,
    AsyncF10Client,
    AsyncGoodsClient,
    AsyncMacClient,
    AsyncTdxClient,
    ExMarketClient,
    F10Client,
    GoodsClient,
    MacClient,
    TdxClient,
    get_client,
)
from atst.errors import ConfigError
from atst.protocol.commands import Family
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool


def _host(family: str) -> HostEntry:
    return HostEntry(host="127.0.0.1", port=7709, family=family)


class _OpaquePool:
    pass


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
    try:
        with pytest.raises(ConfigError, match="client/pool family 不匹配"):
            AsyncF10Client(pool=pool)
    finally:
        asyncio.run(pool.close())


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
        asyncio.run(async_pool.close())


def test_opaque_legacy_pool_double_remains_supported_for_canonical_family() -> None:
    sync_client = TdxClient(pool=_OpaquePool())
    async_client = AsyncTdxClient(pool=_OpaquePool())

    assert sync_client.family == Family.STANDARD
    assert async_client.family == Family.STANDARD


def test_opaque_pool_cannot_bypass_requested_family_validation() -> None:
    with pytest.raises(ConfigError, match="client family 非法"):
        TdxClient(pool=_OpaquePool(), family="unknown")
    with pytest.raises(ConfigError, match="client family 非法"):
        AsyncTdxClient(pool=_OpaquePool(), family="unknown")


def test_declared_none_pool_family_is_not_treated_as_opaque() -> None:
    class InvalidPool:
        family = None

    with pytest.raises(ConfigError, match="client/pool family 不匹配"):
        TdxClient(pool=InvalidPool())
    with pytest.raises(ConfigError, match="client/pool family 不匹配"):
        AsyncTdxClient(pool=InvalidPool())


def test_factory_cannot_bypass_pool_family_binding() -> None:
    pool = ConnectionPool(
        [_host(Family.STANDARD)],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        with pytest.raises(ConfigError, match="client/pool family 不匹配"):
            get_client("f10", pool=pool)
    finally:
        pool.close()


def test_injected_pool_rejects_nonempty_hosts_for_sync_and_async() -> None:
    with pytest.raises(ConfigError, match="不能同时传 hosts"):
        TdxClient(hosts=["1.2.3.4:7709"], pool=_OpaquePool())
    with pytest.raises(ConfigError, match="不能同时传 hosts"):
        AsyncTdxClient(hosts=["1.2.3.4:7709"], pool=_OpaquePool())


def test_injected_pool_rejects_nondefault_max_retries_for_sync_and_async() -> None:
    with pytest.raises(ConfigError, match="不能覆盖 max_retries"):
        TdxClient(pool=_OpaquePool(), max_retries=4)
    with pytest.raises(ConfigError, match="不能覆盖 max_retries"):
        AsyncTdxClient(pool=_OpaquePool(), max_retries=4)


def test_injected_pool_rejects_pool_constructor_kwargs_for_sync_and_async() -> None:
    with pytest.raises(ConfigError, match="slots_per_host"):
        TdxClient(pool=_OpaquePool(), slots_per_host=2)
    with pytest.raises(ConfigError, match="slots_per_host"):
        AsyncTdxClient(pool=_OpaquePool(), slots_per_host=2)


def test_injected_pool_keeps_client_request_timeout_semantics() -> None:
    sync_client = TdxClient(pool=_OpaquePool(), timeout=2.5)
    async_client = AsyncTdxClient(pool=_OpaquePool(), timeout=2.5)

    assert sync_client.timeout == 2.5
    assert async_client.timeout == 2.5


@pytest.mark.parametrize(
    ("client_cls", "expected_family", "conflicting_family"),
    [
        (GoodsClient, Family.GOODS, Family.STANDARD),
        (ExMarketClient, Family.EXTENDED, Family.STANDARD),
        (MacClient, Family.MAC, Family.STANDARD),
        (F10Client, Family.F10, Family.STANDARD),
        (AsyncGoodsClient, Family.GOODS, Family.STANDARD),
        (AsyncExMarketClient, Family.EXTENDED, Family.STANDARD),
        (AsyncMacClient, Family.MAC, Family.STANDARD),
        (AsyncF10Client, Family.F10, Family.STANDARD),
    ],
)
def test_family_specific_clients_reject_conflicting_explicit_family(
    client_cls: type[Any],
    expected_family: str,
    conflicting_family: str,
) -> None:
    with pytest.raises(ConfigError, match="固定协议族客户端"):
        client_cls(pool=_OpaquePool(), family=conflicting_family)

    client = client_cls(pool=_OpaquePool(), family=expected_family)
    assert client.family == expected_family
