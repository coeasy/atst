# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""内置聚合/常量（无自有传输，仅统一 QuerySpec 路径可达）——Provider 声明（v2 注册表收敛）。

本模块是 ``builtin`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="builtin",
    channels=(_c("catalog", "dc_reports", "index_list", "trade_calendar"),),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {}
EXPLICIT_CHANNELS: frozenset[str] = frozenset()
DIRECT_CHANNELS: frozenset[str] = frozenset()
#: 复合 Provider 没有自有传输：仅统一 QuerySpec 路径可达，无 channel adapter。
CHANNEL_API_EXEMPT = True

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="dc_reports",
        provider="builtin",
        channel="catalog",
        backend="web_session",
        method="dc_reports",
        source="sina",
    ),
    CapabilityBinding(
        capability="index_list",
        provider="builtin",
        channel="catalog",
        backend="web_session",
        method="index_list",
        source="sina",
    ),
    CapabilityBinding(
        capability="trade_calendar",
        provider="builtin",
        channel="catalog",
        backend="web_session",
        method="trade_calendar",
        source="sina",
    ),
)
