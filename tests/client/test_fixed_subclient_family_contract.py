from __future__ import annotations

from typing import Any

import pytest

from atst.client import (
    AsyncExMarketClient,
    AsyncF10Client,
    AsyncGoodsClient,
    AsyncMacClient,
    ExMarketClient,
    F10Client,
    GoodsClient,
    MacClient,
    get_client,
)
from atst.errors import ConfigError
from atst.protocol.commands import Family


class OpaquePool:
    pass


_FIXED = (
    (GoodsClient, AsyncGoodsClient, Family.GOODS),
    (ExMarketClient, AsyncExMarketClient, Family.EXTENDED),
    (MacClient, AsyncMacClient, Family.MAC),
    (F10Client, AsyncF10Client, Family.F10),
)


@pytest.mark.parametrize(("sync_cls", "async_cls", "required_family"), _FIXED)
def test_fixed_subclients_reject_conflicting_explicit_family(
    sync_cls: type[Any],
    async_cls: type[Any],
    required_family: str,
) -> None:
    conflicting = Family.STANDARD if required_family != Family.STANDARD else Family.F10

    with pytest.raises(ConfigError, match="固定协议族客户端不接受冲突 family"):
        sync_cls(pool=OpaquePool(), family=conflicting)
    with pytest.raises(ConfigError, match="固定协议族客户端不接受冲突 family"):
        async_cls(pool=OpaquePool(), family=conflicting)


@pytest.mark.parametrize(("sync_cls", "async_cls", "required_family"), _FIXED)
def test_fixed_subclients_accept_matching_explicit_family(
    sync_cls: type[Any],
    async_cls: type[Any],
    required_family: str,
) -> None:
    sync_client = sync_cls(pool=OpaquePool(), family=required_family)
    async_client = async_cls(pool=OpaquePool(), family=required_family)

    assert sync_client.family == required_family
    assert async_client.family == required_family
    assert sync_cls.__init__.__module__ == "atst.client.sync"
    assert async_cls.__init__.__module__ == "atst.client.async_"


def test_factory_rejects_conflicting_fixed_family_override() -> None:
    with pytest.raises(ConfigError, match="固定协议族客户端不接受冲突 family"):
        get_client("f10", pool=OpaquePool(), family=Family.STANDARD)
