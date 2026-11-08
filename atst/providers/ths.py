# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""同花顺（涨停池/板块归属/人气榜）——Provider 声明（v2 注册表收敛）。

本模块是 ``ths`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="ths",
    channels=(_c("catalog", "concept_members", "hot_rank", "limit_pool", "theme_attribution"),),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "catalog": ("atst.sources.ths.adapters", "ThsSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="concept_members",
        provider="ths",
        channel="catalog",
        backend="web_adapter",
        method="fetch_concept_members",
        factory="atst.sources.ths.adapters:ThsSource",
    ),
    CapabilityBinding(
        capability="hot_rank",
        provider="ths",
        channel="catalog",
        backend="web_adapter",
        method="fetch_hot_rank",
        factory="atst.sources.ths.adapters:ThsSource",
    ),
    CapabilityBinding(
        capability="limit_pool",
        provider="ths",
        channel="catalog",
        backend="web_adapter",
        method="fetch_limit_pool",
        factory="atst.sources.ths.adapters:ThsSource",
    ),
    CapabilityBinding(
        capability="theme_attribution",
        provider="ths",
        channel="catalog",
        backend="web_adapter",
        method="fetch_theme_attribution",
        factory="atst.sources.ths.adapters:ThsSource",
    ),
)
