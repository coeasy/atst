# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the canonical v13/v16 Client.

Typed queries describe business intent without bypassing the canonical
Provider/Channel/Capability registry. A typed capability is executable through
:meth:`tstdx.Client.typed` only after the same capability is present in
``tstdx.providers.PROVIDERS``; contracts for data-source methods that are not yet
registered therefore remain pending automatically instead of creating a second
capability namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Generic, TypeVar

from .errors import ValidationError
from .providers import PROVIDERS

T = TypeVar("T")


def _domain_record_cls() -> dict[str, type]:
    """Lazy-load Domain Record 注册表（延迟避免 import 环）。"""
    from .domain.records import (
        BondRecord,
        FinancialRecord,
        FundRecord,
        MacroRecord,
        MarketDataRecord,
        NewsRecord,
        OptionRecord,
        ResearchRecord,
        SearchRecord,
    )

    return {
        # Financial（含 v13 原始三表）
        "balance_sheet": FinancialRecord,
        "income_sheet": FinancialRecord,
        "cash_flow": FinancialRecord,
        "financial_abstract": FinancialRecord,
        "dividend_history": FinancialRecord,
        "stock_valuation": FinancialRecord,
        "holder_changes": FinancialRecord,
        "holder_num": FinancialRecord,
        "free_holders": FinancialRecord,
        "capital_changes": FinancialRecord,
        "corporate_action": FinancialRecord,
        "announcements": FinancialRecord,
        "ipo_review": FinancialRecord,
        "stock_base_info": FinancialRecord,
        "stock_all_performance": FinancialRecord,
        "stock_report_dates": FinancialRecord,
        # Fund
        "fund_rank": FundRecord,
        "fund_holdings": FundRecord,
        "fund_base_info": FundRecord,
        "fund_base_info_multi": FundRecord,
        "fund_manager": FundRecord,
        "fund_asset_allocation": FundRecord,
        "fund_period_change": FundRecord,
        "fund_industry_distribution": FundRecord,
        "fund_public_dates": FundRecord,
        # Bond
        "bond_kline": BondRecord,
        "bond_base_info": BondRecord,
        "bond_all_base_info": BondRecord,
        "bond_realtime": BondRecord,
        "bond_trades": BondRecord,
        "bond_today_bill": BondRecord,
        "bond_history_bill": BondRecord,
        "convertible_bond": BondRecord,
        # Futures
        "futures_kline": BondRecord,
        "futures_base_info": BondRecord,
        "futures_realtime": BondRecord,
        "futures_trades": BondRecord,
        # Options
        "options_snapshot": OptionRecord,
        "options_list": OptionRecord,
        "options_trends": OptionRecord,
        # News / Research
        "news_financial": NewsRecord,
        "news": NewsRecord,
        "research_reports": ResearchRecord,
        "research_visits": ResearchRecord,
        # Market data
        "hot_rank": MarketDataRecord,
        "limit_pool": MarketDataRecord,
        "northbound": MarketDataRecord,
        "margin": MarketDataRecord,
        "longhu": MarketDataRecord,
        "market_stat": MarketDataRecord,
        "board_rank": MarketDataRecord,
        "fund_flow": MarketDataRecord,
        "stock_changes": MarketDataRecord,
        "rank": MarketDataRecord,
        # Search
        "wencai": SearchRecord,
        "screening": SearchRecord,
        "suggest": SearchRecord,
        "index_constituents": SearchRecord,
        "industry_board": MarketDataRecord,
        "board_list": MarketDataRecord,
        "board_member": MarketDataRecord,
        # Macro
        "fx_rates": MacroRecord,
        "global_quotes": MacroRecord,
        # F10 公司档案（含基本信息/股东/主营等 metrics）
        "f10": FinancialRecord,
    }


_DOMAIN_RECORD_CACHE: dict[str, type] | None = None


def record_type_for(capability: str) -> type | None:
    """返回 capability 对应的 Domain Record 类型（未注册返回 None）。"""
    global _DOMAIN_RECORD_CACHE
    if _DOMAIN_RECORD_CACHE is None:
        _DOMAIN_RECORD_CACHE = _domain_record_cls()
    return _DOMAIN_RECORD_CACHE.get(str(capability).strip().lower())


def records_from_data(capability: str, data: Any) -> list[Any]:
    """把 Typed Query 执行结果归一化为 Domain Record 列表。

    - dict / list[dict] 业务结果 -> Record 列表
    - 已是 Record -> 原样
    - capability 未注册 Domain Record -> 返回原始数据（list 包装）
    """
    record_cls = record_type_for(capability)
    if record_cls is None:
        return [data] if not isinstance(data, list) else list(data)
    from .domain.records import normalize_to_records

    return normalize_to_records(data, record_cls)


_RESERVED_QUERY_FIELDS = frozenset({"capability", "provider", "options"})


def call_payload_from_typed(query: CapabilityQuery) -> dict[str, Any]:
    """Compile one immutable typed query into fail-closed ``Client.call`` kwargs.

    Refuses contracts that are not registered in the canonical Provider
    registry, so typed queries cannot become a second capability namespace.
    """
    if not isinstance(query, CapabilityQuery):
        raise TypeError("query must be a CapabilityQuery")
    if not query.semantic_ready:
        raise ValidationError(
            f"typed capability {query.capability!r} is not registered for semantic runtime execution",
            context={
                "capability": query.capability,
                "semantic_ready": False,
            },
        )
    params = dict(query.options)
    for item in fields(query):
        if item.name in _RESERVED_QUERY_FIELDS:
            continue
        params[item.name] = getattr(query, item.name)
    return params


def records_from_response(query: CapabilityQuery, response: Any) -> list[Any]:
    """从一次 Typed Query 的执行结果提取类型化 Domain Record 列表。

    ``response`` 需具备 ``data`` 属性（如 :class:`tstdx.result.QueryResult`）。
    ``None`` 结果返回空列表；执行失败在 Client 世界以异常表达。
    """
    if response is None:
        return []
    return records_from_data(query.capability, getattr(response, "data", None))


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    capability: str
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def semantic_ready(self) -> bool:
        """Whether the canonical Provider registry can satisfy this capability."""
        try:
            if self.provider:
                return PROVIDERS.supports(self.provider, self.capability)
            return any(
                PROVIDERS.supports(provider, self.capability) for provider in PROVIDERS.ids()
            )
        except ValidationError:
            return False


@dataclass(frozen=True, slots=True)
class SymbolQuery(CapabilityQuery):
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class BatchCapabilityQuery(CapabilityQuery):
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BalanceSheetQuery(SymbolQuery):
    capability: str = "balance_sheet"


@dataclass(frozen=True, slots=True)
class IncomeStatementQuery(SymbolQuery):
    capability: str = "income_sheet"


@dataclass(frozen=True, slots=True)
class CashFlowQuery(SymbolQuery):
    capability: str = "cash_flow"


@dataclass(frozen=True, slots=True)
class FundRankQuery(CapabilityQuery):
    capability: str = "fund_rank"
    fund_type: int = 0


@dataclass(frozen=True, slots=True)
class FundHoldingsQuery(CapabilityQuery):
    """Kernel contract: ``fund_holdings(code, dates=None)``."""

    capability: str = "fund_holdings"
    code: str = ""


@dataclass(frozen=True, slots=True)
class BondKlineQuery(CapabilityQuery):
    """Kernel contract: ``bond_kline(code, *, period, count, adjust)``."""

    capability: str = "bond_kline"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FuturesKlineQuery(CapabilityQuery):
    """Kernel contract: ``futures_kline(quote_id, *, period, count, adjust)``."""

    capability: str = "futures_kline"
    quote_id: str = ""


@dataclass(frozen=True, slots=True)
class OptionSnapshotQuery(CapabilityQuery):
    """Kernel contract: ``options_snapshot(quote_id)``."""

    capability: str = "options_snapshot"
    quote_id: str = ""


@dataclass(frozen=True, slots=True)
class NewsQuery(CapabilityQuery):
    capability: str = "news_financial"
    page: int = 1
    size: int = 30


@dataclass(frozen=True, slots=True)
class ResearchReportQuery(SymbolQuery):
    capability: str = "research_reports"


@dataclass(frozen=True, slots=True)
class F10Query(SymbolQuery):
    """``filename`` mirrors the kernel ``f10`` contract (F10Client.download)."""

    capability: str = "f10"
    filename: str = ""


@dataclass(frozen=True, slots=True)
class TypedQueryResult(Generic[T]):
    """Typed payload wrapper carried inside canonical QueryResult."""

    data: T
    capability: str


# --------------------------------------------------------------------------- #
# 领域化 Typed Query 契约（v14 Phase 1 扩展）
# --------------------------------------------------------------------------- #
# 每新增一个 Typed Query 契约，capability 必须与 tstdx.providers.PROVIDERS
# 注册表一致（semantic_ready 为 True），否则 call_payload_from_typed 会拒绝执行。


@dataclass(frozen=True, slots=True)
class FinancialQuery(SymbolQuery):
    """财务数据领域基类（symbol-based）。"""


@dataclass(frozen=True, slots=True)
class FinancialAbstractQuery(FinancialQuery):
    capability: str = "financial_abstract"


@dataclass(frozen=True, slots=True)
class DividendHistoryQuery(FinancialQuery):
    capability: str = "dividend_history"


@dataclass(frozen=True, slots=True)
class StockValuationQuery(FinancialQuery):
    capability: str = "stock_valuation"


@dataclass(frozen=True, slots=True)
class HolderChangesQuery(FinancialQuery):
    capability: str = "holder_changes"


@dataclass(frozen=True, slots=True)
class HolderNumQuery(FinancialQuery):
    capability: str = "holder_num"


@dataclass(frozen=True, slots=True)
class FreeHoldersQuery(FinancialQuery):
    capability: str = "free_holders"


@dataclass(frozen=True, slots=True)
class CapitalChangesQuery(FinancialQuery):
    capability: str = "capital_changes"


@dataclass(frozen=True, slots=True)
class CorporateActionQuery(FinancialQuery):
    capability: str = "corporate_action"


@dataclass(frozen=True, slots=True)
class AnnouncementsQuery(BatchCapabilityQuery):
    """Kernel contract: ``announcements(symbols, *, page, size)``."""

    capability: str = "announcements"


@dataclass(frozen=True, slots=True)
class IpoReviewQuery(CapabilityQuery):
    """Kernel contract: ``ipo_review(*, page, size)`` — 无 symbol 参数。"""

    capability: str = "ipo_review"


@dataclass(frozen=True, slots=True)
class StockBaseInfoQuery(CapabilityQuery):
    """Kernel contract: ``stock_base_info(codes)``。"""

    capability: str = "stock_base_info"
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StockAllPerformanceQuery(CapabilityQuery):
    """Kernel contract: ``stock_all_performance(report_date='')``。"""

    capability: str = "stock_all_performance"
    report_date: str = ""


@dataclass(frozen=True, slots=True)
class StockReportDatesQuery(CapabilityQuery):
    """Kernel contract: ``stock_report_dates(limit=100)`` — 无 symbol 参数。"""

    capability: str = "stock_report_dates"


@dataclass(frozen=True, slots=True)
class FundQuery(CapabilityQuery):
    """基金数据领域基类（code-based，非 symbol）。"""


@dataclass(frozen=True, slots=True)
class FundBaseInfoQuery(FundQuery):
    capability: str = "fund_base_info"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FundBaseInfoMultiQuery(FundQuery):
    capability: str = "fund_base_info_multi"
    codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.codes:
            raise ValidationError(
                "fund_base_info_multi 必须提供 codes",
                context={"capability": "fund_base_info_multi"},
            )


@dataclass(frozen=True, slots=True)
class FundManagerQuery(FundQuery):
    capability: str = "fund_manager"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FundAssetAllocationQuery(FundQuery):
    capability: str = "fund_asset_allocation"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FundPeriodChangeQuery(FundQuery):
    capability: str = "fund_period_change"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FundIndustryDistributionQuery(FundQuery):
    capability: str = "fund_industry_distribution"
    code: str = ""


@dataclass(frozen=True, slots=True)
class FundPublicDatesQuery(FundQuery):
    capability: str = "fund_public_dates"
    code: str = ""


@dataclass(frozen=True, slots=True)
class BondQuery(CapabilityQuery):
    """债券数据领域基类（字段名与内核方法签名逐一对齐）。"""


@dataclass(frozen=True, slots=True)
class BondBaseInfoQuery(BondQuery):
    """Kernel contract: ``bond_base_info(codes)``."""

    capability: str = "bond_base_info"
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BondAllBaseInfoQuery(BondQuery):
    """Kernel contract: ``bond_all_base_info()`` — 全量快照，无参数。"""

    capability: str = "bond_all_base_info"


@dataclass(frozen=True, slots=True)
class BondRealtimeQuery(BondQuery):
    """Kernel contract: ``bond_realtime(codes)``."""

    capability: str = "bond_realtime"
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BondTradesQuery(BondQuery):
    capability: str = "bond_trades"
    code: str = ""


@dataclass(frozen=True, slots=True)
class BondTodayBillQuery(BondQuery):
    capability: str = "bond_today_bill"
    code: str = ""


@dataclass(frozen=True, slots=True)
class BondHistoryBillQuery(BondQuery):
    capability: str = "bond_history_bill"
    code: str = ""


@dataclass(frozen=True, slots=True)
class ConvertibleBondQuery(BondQuery):
    """Kernel contract: JSL ``fetch(symbols, **kwargs)``."""

    capability: str = "convertible_bond"
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FuturesQuery(CapabilityQuery):
    """期货数据领域基类（字段名与内核方法签名对齐）。"""


@dataclass(frozen=True, slots=True)
class FuturesBaseInfoQuery(FuturesQuery):
    """Kernel contract: ``futures_base_info()`` — 全量列表，无参数。"""

    capability: str = "futures_base_info"


@dataclass(frozen=True, slots=True)
class FuturesRealtimeQuery(FuturesQuery):
    capability: str = "futures_realtime"
    quote_id: str = ""


@dataclass(frozen=True, slots=True)
class FuturesTradesQuery(FuturesQuery):
    capability: str = "futures_trades"
    quote_id: str = ""


@dataclass(frozen=True, slots=True)
class OptionsQuery(CapabilityQuery):
    """期权数据领域基类（字段名与内核方法签名对齐）。"""


@dataclass(frozen=True, slots=True)
class OptionsListQuery(OptionsQuery):
    """Kernel contract: ``options_list(*, market, size, page)`` — 无 symbol 参数。"""

    capability: str = "options_list"


@dataclass(frozen=True, slots=True)
class OptionsTrendsQuery(OptionsQuery):
    capability: str = "options_trends"
    quote_id: str = ""


@dataclass(frozen=True, slots=True)
class ResearchVisitsQuery(SymbolQuery):
    capability: str = "research_visits"


@dataclass(frozen=True, slots=True)
class MarketDataQuery(CapabilityQuery):
    """市场数据/行情衍生领域基类。"""


@dataclass(frozen=True, slots=True)
class HotRankQuery(MarketDataQuery):
    capability: str = "hot_rank"


@dataclass(frozen=True, slots=True)
class LimitPoolQuery(MarketDataQuery):
    capability: str = "limit_pool"


@dataclass(frozen=True, slots=True)
class NorthboundQuery(MarketDataQuery):
    capability: str = "northbound"


@dataclass(frozen=True, slots=True)
class MarginQuery(MarketDataQuery):
    capability: str = "margin"
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class LonghuQuery(MarketDataQuery):
    capability: str = "longhu"


@dataclass(frozen=True, slots=True)
class MarketStatQuery(MarketDataQuery):
    capability: str = "market_stat"


@dataclass(frozen=True, slots=True)
class BoardRankQuery(MarketDataQuery):
    capability: str = "board_rank"


@dataclass(frozen=True, slots=True)
class FundFlowQuery(MarketDataQuery):
    """Kernel contract: ``fund_flow(symbols)``。"""

    capability: str = "fund_flow"
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StockChangesQuery(MarketDataQuery):
    capability: str = "stock_changes"


@dataclass(frozen=True, slots=True)
class RankQuery(MarketDataQuery):
    capability: str = "rank"


@dataclass(frozen=True, slots=True)
class SearchQuery(CapabilityQuery):
    """选股/搜索领域基类。"""


@dataclass(frozen=True, slots=True)
class WencaiQuery(SearchQuery):
    """Kernel contract: ``wencai(query, *, page, limit, cookie)``."""

    capability: str = "wencai"
    query: str = ""

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValidationError("wencai 查询必须提供 query", context={"capability": "wencai"})


@dataclass(frozen=True, slots=True)
class ScreeningQuery(SearchQuery):
    """Kernel contract: ``fetch_strategy(query, *, page, limit)``."""

    capability: str = "screening"
    query: str = ""

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValidationError(
                "screening 查询必须提供 query", context={"capability": "screening"}
            )


@dataclass(frozen=True, slots=True)
class SuggestQuery(SearchQuery):
    """Kernel contract: sina ``suggest(key, ...)``。"""

    capability: str = "suggest"
    key: str = ""

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValidationError("suggest 查询必须提供 key", context={"capability": "suggest"})


@dataclass(frozen=True, slots=True)
class IndexConstituentsQuery(SearchQuery):
    """Kernel contract: ``index_constituents(index, ...)``。"""

    capability: str = "index_constituents"
    index: str = ""

    def __post_init__(self) -> None:
        if not self.index.strip():
            raise ValidationError(
                "index_constituents 查询必须提供 index",
                context={"capability": "index_constituents"},
            )


@dataclass(frozen=True, slots=True)
class IndustryBoardQuery(SearchQuery):
    capability: str = "industry_board"


@dataclass(frozen=True, slots=True)
class BoardListQuery(SearchQuery):
    capability: str = "board_list"


@dataclass(frozen=True, slots=True)
class BoardMemberQuery(SearchQuery):
    """Kernel contract: sina ``fetch_members(node, *, page_size, max_pages)``。"""

    capability: str = "board_member"
    node: str = ""

    def __post_init__(self) -> None:
        if not self.node.strip():
            raise ValidationError(
                "board_member 查询必须提供 node", context={"capability": "board_member"}
            )


@dataclass(frozen=True, slots=True)
class MacroQuery(CapabilityQuery):
    """宏观/全球数据领域基类。"""


@dataclass(frozen=True, slots=True)
class FxRatesQuery(MacroQuery):
    capability: str = "fx_rates"


@dataclass(frozen=True, slots=True)
class GlobalQuotesQuery(MacroQuery):
    """Kernel contract: Tencent global ``fetch(symbols, **kwargs)``。"""

    capability: str = "global_quotes"
    symbols: tuple[str, ...] = ()
