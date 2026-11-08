# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""百度财经 HTTP 源（gushitong；仅 A 股）——Provider 声明（v2 注册表收敛）。

本模块是 ``baidu`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="baidu",
    channels=(
        _c("quote", "quotes", live=True),
        _c("kline", "bars", periods=("day", "month", "week")),
        _c("minute", "minute", live=True),
        _c("ticks", "trades", live=True),
        _c(
            "catalog",
            "baidu_kline",
            "baidu_minute",
            "baidu_quote",
            "baidu_ticks",
            "baidu_valuation_history",
        ),
    ),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "catalog": ("atst.sources.session", "WebQuoteSession"),
    "kline": ("atst.sources.baidu.adapters", "BaiduSource"),
    "minute": ("atst.sources.baidu.adapters", "BaiduSource"),
    "quote": ("atst.sources.baidu.adapters", "BaiduSource"),
    "ticks": ("atst.sources.baidu.adapters", "BaiduSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="baidu_kline",
        provider="baidu",
        channel="catalog",
        backend="web_session",
        method="baidu_kline",
        source="sina",
    ),
    CapabilityBinding(
        capability="baidu_minute",
        provider="baidu",
        channel="catalog",
        backend="web_session",
        method="baidu_minute",
        source="sina",
    ),
    CapabilityBinding(
        capability="baidu_quote",
        provider="baidu",
        channel="catalog",
        backend="web_session",
        method="baidu_quote",
        source="sina",
    ),
    CapabilityBinding(
        capability="baidu_ticks",
        provider="baidu",
        channel="catalog",
        backend="web_session",
        method="baidu_ticks",
        source="sina",
    ),
    CapabilityBinding(
        capability="baidu_valuation_history",
        provider="baidu",
        channel="catalog",
        backend="web_session",
        method="baidu_valuation_history",
        source="sina",
    ),
    CapabilityBinding(
        capability="minute",
        provider="baidu",
        channel="minute",
        backend="direct_adapter",
        method="fetch_minute",
    ),
    CapabilityBinding(
        capability="trades",
        provider="baidu",
        channel="ticks",
        backend="direct_adapter",
        method="fetch_ticks",
    ),
)
