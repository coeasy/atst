# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""巨潮资讯（法定披露公告）——Provider 声明（v2 注册表收敛）。

本模块是 ``cninfo`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="cninfo",
    channels=(_c("catalog", "announcements", "hk_announcements", "interactive_qa"),),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "catalog": ("atst.sources.cninfo.adapters", "CninfoSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {
    "interactive_qa": "needs_verify",
}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="announcements",
        provider="cninfo",
        channel="catalog",
        backend="web_adapter",
        method="fetch_announcements",
        factory="atst.sources.cninfo.adapters:CninfoSource",
    ),
    CapabilityBinding(
        capability="hk_announcements",
        provider="cninfo",
        channel="catalog",
        backend="web_adapter",
        method="fetch_hk_announcements",
        factory="atst.sources.cninfo.adapters:CninfoSource",
    ),
    CapabilityBinding(
        capability="interactive_qa",
        provider="cninfo",
        channel="catalog",
        backend="web_session",
        method="interactive_qa",
        source="sina",
    ),
)
