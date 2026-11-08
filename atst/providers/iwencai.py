# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""同花顺 i问财（自然语言选股）——Provider 声明（v2 注册表收敛）。

本模块是 ``iwencai`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="iwencai",
    channels=(_c("screening", "screening", "wencai"),),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "screening": ("atst.sources.wencai", "WencaiSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="screening",
        provider="iwencai",
        channel="screening",
        backend="direct_adapter",
        method="fetch_strategy",
    ),
    CapabilityBinding(
        capability="wencai",
        provider="iwencai",
        channel="screening",
        backend="web_session",
        method="wencai",
        source="sina",
    ),
)
