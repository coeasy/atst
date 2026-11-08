# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""新浪财经 HTTP 源（hq.sinajs.cn 等）——Provider 声明（v2 注册表收敛）。

本模块是 ``sina`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="sina",
    channels=(
        _c("quote", "quotes", live=True),
        _c(
            "history_kline",
            "bars",
            periods=("1200min", "120min", "15min", "30min", "5min", "60min", "day"),
        ),
        _c("suggest", "suggest"),
        _c("industry_board", "industry_board"),
        _c("board_list", "board_list"),
        _c("board_member", "board_member"),
        _c("fund_flow", "fund_flow"),
        _c("news", "news"),
        _c(
            "catalog",
            "all_market",
            "board_members",
            "esg_history",
            "esg_rating",
            "esg_ratings_all",
            "history",
            "hk_quotes",
            "industry_boards",
            "search_symbols",
        ),
    ),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {
    "board_list": ("atst.sources.boards", "SinaBoardListSource"),
    "board_member": ("atst.sources.boards", "SinaBoardMemberSource"),
    "catalog": ("atst.sources.session", "WebQuoteSession"),
    "fund_flow": ("atst.sources.fundflow", "SinaFundFlowSource"),
    "history_kline": ("atst.sources.sina.adapters", "SinaHistoryKlineSource"),
    "industry_board": ("atst.sources.boards", "SinaIndustryBoardSource"),
    "news": ("atst.sources.news", "SinaNewsSource"),
    "quote": ("atst.sources.sina.adapters", "SinaSource"),
    "suggest": ("atst.sources.sina.adapters", "SuggestSource"),
}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="board_list",
        provider="sina",
        channel="board_list",
        backend="web_session",
        method="board_list",
        source="sina",
    ),
    CapabilityBinding(
        capability="board_member",
        provider="sina",
        channel="board_member",
        backend="direct_adapter",
        method="fetch_members",
    ),
    CapabilityBinding(
        capability="all_market",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="all_market",
        source="sina",
    ),
    CapabilityBinding(
        capability="board_members",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="board_members",
        source="sina",
    ),
    CapabilityBinding(
        capability="esg_history",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="esg_history",
        source="sina",
    ),
    CapabilityBinding(
        capability="esg_rating",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="esg_rating",
        source="sina",
    ),
    CapabilityBinding(
        capability="esg_ratings_all",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="esg_ratings_all",
        source="sina",
    ),
    CapabilityBinding(
        capability="history",
        provider="sina",
        channel="catalog",
        backend="web_adapter",
        method="fetch_bars",
        factory="atst.sources.sina.adapters:SinaHistoryKlineSource",
    ),
    CapabilityBinding(
        capability="hk_quotes",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="hk_quotes",
        source="sina",
    ),
    CapabilityBinding(
        capability="industry_boards",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="industry_boards",
        source="sina",
    ),
    CapabilityBinding(
        capability="search_symbols",
        provider="sina",
        channel="catalog",
        backend="web_session",
        method="search_symbols",
        source="sina",
    ),
    CapabilityBinding(
        capability="fund_flow",
        provider="sina",
        channel="fund_flow",
        backend="web_session",
        method="fund_flow",
        source="sina",
    ),
    CapabilityBinding(
        capability="industry_board",
        provider="sina",
        channel="industry_board",
        backend="direct_adapter",
        method="fetch_boards",
    ),
    CapabilityBinding(
        capability="news",
        provider="sina",
        channel="news",
        backend="web_session",
        method="news",
        source="sina",
    ),
    CapabilityBinding(
        capability="suggest",
        provider="sina",
        channel="suggest",
        backend="web_session",
        method="suggest",
        source="sina",
    ),
)
