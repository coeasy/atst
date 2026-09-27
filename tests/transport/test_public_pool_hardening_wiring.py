from __future__ import annotations

import importlib.util

import pytest

import atst.transport.hosts as hosts_module
from atst.transport import ConnectionPool, RankingStore
from atst.transport.async_ import AsyncConnectionPool

#: 曾被侧车桩层整方法替换的实现。它们必须住在公开类自己的模块里，
#: 否则模块里的原实现就是永远执行不到的死代码，而覆盖率会把它读成「已测」。
POOL_IMPLEMENTATIONS = {
    ConnectionPool: (
        "atst.transport.pool",
        ("request", "request_multi", "iter_frames", "update_hosts", "_start_heartbeat"),
    ),
    AsyncConnectionPool: (
        "atst.transport.async_",
        ("request", "request_multi", "iter_frames", "update_hosts", "close", "_heartbeat_loop"),
    ),
}

#: 已解散的整方法替换桩层：模块文件本身必须不存在。
DISSOLVED_PATCH_LAYERS = (
    "atst.transport._pool_hardening",
    "atst.transport._async_pool_hardening",
    "atst.transport._async_close_hardening",
    "atst.transport._pool_provenance_hardening",
)


@pytest.mark.unit
@pytest.mark.parametrize("cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_implementations_live_in_their_canonical_module(cls: type) -> None:
    module, names = POOL_IMPLEMENTATIONS[cls]
    for name in names:
        impl = getattr(cls, name)
        assert impl.__module__ == module, f"{cls.__name__}.{name} 由 {impl.__module__} 提供"
        assert getattr(impl, "__wrapped__", None) is None, f"{cls.__name__}.{name} 仍是转发桩"


@pytest.mark.unit
def test_dissolved_patch_layers_are_physically_gone() -> None:
    for name in DISSOLVED_PATCH_LAYERS:
        assert importlib.util.find_spec(name) is None, f"{name} 尚未删除"


@pytest.mark.unit
def test_public_ranking_store_wiring_is_canonical() -> None:
    assert RankingStore.load.__module__ == "atst.transport.hosts"
    assert RankingStore.save.__module__ == "atst.transport.hosts"
    assert RankingStore.merge.__module__ == "atst.transport.hosts"
    assert hosts_module._apply_ranked_observation.__module__ == "atst.transport.hosts"
