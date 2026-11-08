# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""腾讯财经 HTTP 源（qt.gtimg.cn 等）——Provider 声明（v2 注册表收敛）。

本模块是 ``tencent`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="tencent",
    channels=(
        _c("quote", "quotes", live=True),
        _c("kline", "bars", periods=("day", "month", "week")),
        _c("minute_kline", "bars", periods=("15min", "1min", "30min", "5min", "60min")),
        _c("minute", "minute", live=True),
        _c("ticks", "trades", live=True),
        _c("global", "global_quotes", live=True),
        _c("market_stat", "market_stat", live=True),
        _c("board_rank", "board_rank", live=True),
        _c(
            "catalog",
            "all_market",
            "globals",
            "hk_quotes",
            "minute_klines",
            "minute_web",
            "ticks",
            "us_quotes",
        ),
    ),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "board_rank": ("atst.sources.boards", "TencentBoardRankSource"),
    "catalog": ("atst.sources.session", "WebQuoteSession"),
    "global": ("atst.sources.global_market", "TencentGlobalSource"),
    "kline": ("atst.sources.tencent.adapters", "KlineSource"),
    "market_stat": ("atst.sources.global_market", "TencentMarketStatSource"),
    "minute": ("atst.sources.tencent.adapters", "MinuteSource"),
    "minute_kline": ("atst.sources.tencent.adapters", "MinuteKlineSource"),
    "quote": ("atst.sources.tencent.adapters", "TencentSource"),
    "ticks": ("atst.sources.ticks", "TencentTickSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ("minute_klines",)
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="board_rank",
        provider="tencent",
        channel="board_rank",
        backend="web_session",
        method="board_rank",
        source="tencent",
    ),
    CapabilityBinding(
        capability="all_market",
        provider="tencent",
        channel="catalog",
        backend="web_session",
        method="all_market",
        source="tencent",
    ),
    CapabilityBinding(
        capability="globals",
        provider="tencent",
        channel="catalog",
        backend="web_session",
        method="globals",
        source="tencent",
    ),
    CapabilityBinding(
        capability="hk_quotes",
        provider="tencent",
        channel="catalog",
        backend="web_session",
        method="hk_quotes",
        source="tencent",
    ),
    CapabilityBinding(
        capability="minute_klines",
        provider="tencent",
        channel="catalog",
        backend="web_adapter",
        method="fetch_bars",
        factory="atst.sources.tencent.adapters:MinuteKlineSource",
    ),
    CapabilityBinding(
        capability="minute_web",
        provider="tencent",
        channel="catalog",
        backend="web_adapter",
        method="fetch_minute",
        factory="atst.sources.tencent.adapters:MinuteSource",
    ),
    CapabilityBinding(
        capability="ticks",
        provider="tencent",
        channel="catalog",
        backend="web_session",
        method="ticks",
        source="tencent",
    ),
    CapabilityBinding(
        capability="us_quotes",
        provider="tencent",
        channel="catalog",
        backend="web_session",
        method="us_quotes",
        source="tencent",
    ),
    CapabilityBinding(
        capability="global_quotes",
        provider="tencent",
        channel="global",
        backend="direct_adapter",
        method="fetch",
    ),
    CapabilityBinding(
        capability="market_stat",
        provider="tencent",
        channel="market_stat",
        backend="web_session",
        method="market_stat",
        source="tencent",
    ),
    CapabilityBinding(
        capability="minute",
        provider="tencent",
        channel="minute",
        backend="direct_adapter",
        method="fetch_minute",
    ),
    CapabilityBinding(
        capability="trades",
        provider="tencent",
        channel="ticks",
        backend="direct_adapter",
        method="fetch_ticks",
    ),
)
