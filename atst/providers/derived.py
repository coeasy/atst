# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""显式聚合（无自有传输；能力名里的 provider 指“取原始数据的那条腿”）——Provider 声明（v2 注册表收敛）。

本模块是 ``derived`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。

P1-4（B5）：catalog 能力在 ``_CATALOG_CAPABILITIES`` 一张能力名表里登记一次，
``SPEC`` 的 channel capabilities 与 ``BINDINGS`` 的逐条绑定都由同一张表派生——
不再用同一个 6 行模板手抄 110 遍。catalog 能力是同一形状的 sina 代理
（backend=web_session / source=sina / method=能力名），声明期校验一个不少：
``providers/_aggregate_bindings`` 仍拒绝重复三元组并强制 web_session 显式登记来源，
``_validate_channel_declarations`` 与 ``audit_runtime`` 三方对账照常。新增 catalog
能力 = 能力名表加一个名字；非 catalog 能力（composed 编排的三条）各是一条显式
绑定，不走生成循环——它们的形状与 catalog 不同，硬塞进同一张表才是第二份口径。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

#: catalog 能力名表（唯一登记处，P1-4/B5）：``SPEC`` 与 ``BINDINGS`` 由它派生。
#: 顺序即重写前声明顺序（字典序），保证文档生成与注册表输出逐字节不变。
_CATALOG_CAPABILITIES: tuple[str, ...] = (
    "all_market",
    "announcements",
    "balance_sheet",
    "big_order_flow",
    "block_trades",
    "bond_all_base_info",
    "bond_base_info",
    "bond_history_bill",
    "bond_kline",
    "bond_realtime",
    "bond_today_bill",
    "bond_trades",
    "breaking_news",
    "cash_flow",
    "chip_distribution",
    "chip_distributions",
    "concept_index",
    "concept_members",
    "convertible_bonds",
    "dc_query",
    "dividend_history",
    "earnings_preview",
    "executive_holds",
    "fin_report",
    "financial_abstract",
    "forecast",
    "free_holders",
    "fund_asset_allocation",
    "fund_base_info",
    "fund_base_info_multi",
    "fund_companies",
    "fund_company_archives",
    "fund_company_base_info",
    "fund_company_funds",
    "fund_company_scale",
    "fund_detail",
    "fund_estimate",
    "fund_flow",
    "fund_flow_history",
    "fund_holdings",
    "fund_industry_distribution",
    "fund_list",
    "fund_manager",
    "fund_manager_eval",
    "fund_manager_list",
    "fund_manager_profile",
    "fund_manager_style",
    "fund_manager_yield",
    "fund_nav_history",
    "fund_nav_history_mob",
    "fund_period_change",
    "fund_public_dates",
    "fund_rank",
    "fund_rank_trend",
    "fund_rating",
    "fund_search",
    "fund_snapshot",
    "fund_yield_curve",
    "futures_base_info",
    "futures_kline",
    "futures_realtime",
    "futures_trades",
    "history",
    "hk_announcements",
    "hk_quotes",
    "holder_changes",
    "holder_num",
    "hot_boards",
    "hot_rank",
    "income_sheet",
    "index",
    "index_constituents",
    "industry_index",
    "ipo_calendar",
    "ipo_review",
    "klines",
    "limit_pool",
    "limit_up_ladder",
    "longhu",
    "macro_cpi",
    "macro_gdp",
    "macro_ppi",
    "margin",
    "market_breadth",
    "news",
    "news_financial",
    "northbound",
    "northbound_hold",
    "notices",
    "options_list",
    "options_snapshot",
    "options_trends",
    "org_profile",
    "org_profiles",
    "performance",
    "profile",
    "rank",
    "rating_consensus",
    "rating_forecast",
    "reports",
    "research_reports",
    "research_visits",
    "rights_issue",
    "sector_flow",
    "shareholder_changes",
    "shareholders",
    "sina_board_fund_flow",
    "sina_fund_flow",
    "stock_all_performance",
    "stock_base_info",
    "stock_changes",
    "stock_report_dates",
    "stock_valuation",
    "theme_attribution",
    "top_holders",
    "unlock_stocks",
    "unlocks",
)

#: catalog 能力的绑定由能力名表生成（P1-4/B5）：同一形状的 sina 代理，仅能力名变化。
_CATALOG_BINDINGS: tuple[CapabilityBinding, ...] = tuple(
    CapabilityBinding(
        capability=capability,
        provider="derived",
        channel="catalog",
        backend="web_session",
        method=capability,
        source="sina",
    )
    for capability in _CATALOG_CAPABILITIES
)

SPEC = ProviderSpec(
    id="derived",
    channels=(
        _c("catalog", *_CATALOG_CAPABILITIES),
        _c("adjustment", "adjusted_bars"),
        _c("sync", "sync_daily"),
        _c("enriched", "daily_enriched"),
    ),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {}
EXPLICIT_CHANNELS: frozenset[str] = frozenset()
DIRECT_CHANNELS: frozenset[str] = frozenset()
#: 复合 Provider 没有自有传输：仅统一 QuerySpec 路径可达，无 channel adapter。
CHANNEL_API_EXEMPT = True

DEFAULT_CAPABILITIES: tuple[str, ...] = ("all_market", "hk_quotes", "history")
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {}

#: composed 编排能力不走生成循环：没有单一 source（真实出数源在内层查询的
#: provenance 里），形状与 catalog 的 sina 代理不同。顺序保持重写前形状：
#: adjusted_bars → catalog（字典序）→ daily_enriched → sync_daily。
BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="adjusted_bars",
        provider="derived",
        channel="adjustment",
        backend="composed",
        method="adjusted_bars",
    ),
    *_CATALOG_BINDINGS,
    CapabilityBinding(
        capability="daily_enriched",
        provider="derived",
        channel="enriched",
        backend="composed",
        method="daily_enriched",
    ),
    CapabilityBinding(
        capability="sync_daily",
        provider="derived",
        channel="sync",
        backend="composed",
        method="sync_daily",
    ),
)
