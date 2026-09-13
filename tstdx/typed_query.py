# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the v14 capability runtime.

Typed queries describe business intent without bypassing the canonical
Provider/Channel/Capability registry. A typed capability is executable through
V14 semantic orchestration only after the same capability is present in
``tstdx.providers.PROVIDERS``; contracts for data-source methods that are not yet
registered therefore remain pending automatically instead of creating a second
capability namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .errors import ValidationError
from .providers import PROVIDERS

T = TypeVar("T")


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
class FundHoldingsQuery(SymbolQuery):
    capability: str = "fund_holdings"


@dataclass(frozen=True, slots=True)
class BondKlineQuery(SymbolQuery):
    capability: str = "bond_kline"


@dataclass(frozen=True, slots=True)
class FuturesKlineQuery(SymbolQuery):
    capability: str = "futures_kline"


@dataclass(frozen=True, slots=True)
class OptionSnapshotQuery(SymbolQuery):
    capability: str = "options_snapshot"


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
    capability: str = "f10"
    section: str = ""


@dataclass(frozen=True, slots=True)
class TypedQueryResult(Generic[T]):
    """Typed payload wrapper carried inside canonical QueryResult."""

    data: T
    capability: str


# --------------------------------------------------------------------------- #
# 领域化 Typed Query 契约（v14 Phase 1 扩展）
# --------------------------------------------------------------------------- #
# 每新增一个 Typed Query 契约，capability 必须与 tstdx.providers.PROVIDERS
# 注册表一致（semantic_ready 为 True），否则 request_from_typed 会拒绝执行。


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
class AnnouncementsQuery(FinancialQuery):
    capability: str = "announcements"


@dataclass(frozen=True, slots=True)
class IpoReviewQuery(FinancialQuery):
    capability: str = "ipo_review"


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
            raise ValidationError("fund_base_info_multi 必须提供 codes", context={"capability": "fund_base_info_multi"})


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
class BondQuery(SymbolQuery):
    """债券数据领域基类。"""


@dataclass(frozen=True, slots=True)
class BondBaseInfoQuery(BondQuery):
    capability: str = "bond_base_info"


@dataclass(frozen=True, slots=True)
class BondAllBaseInfoQuery(BondQuery):
    capability: str = "bond_all_base_info"


@dataclass(frozen=True, slots=True)
class BondRealtimeQuery(BondQuery):
    capability: str = "bond_realtime"


@dataclass(frozen=True, slots=True)
class BondTradesQuery(BondQuery):
    capability: str = "bond_trades"


@dataclass(frozen=True, slots=True)
class BondTodayBillQuery(BondQuery):
    capability: str = "bond_today_bill"


@dataclass(frozen=True, slots=True)
class BondHistoryBillQuery(BondQuery):
    capability: str = "bond_history_bill"


@dataclass(frozen=True, slots=True)
class ConvertibleBondQuery(BondQuery):
    capability: str = "convertible_bond"


@dataclass(frozen=True, slots=True)
class FuturesQuery(SymbolQuery):
    """期货数据领域基类。"""


@dataclass(frozen=True, slots=True)
class FuturesBaseInfoQuery(FuturesQuery):
    capability: str = "futures_base_info"


@dataclass(frozen=True, slots=True)
class FuturesRealtimeQuery(FuturesQuery):
    capability: str = "futures_realtime"


@dataclass(frozen=True, slots=True)
class FuturesTradesQuery(FuturesQuery):
    capability: str = "futures_trades"


@dataclass(frozen=True, slots=True)
class OptionsQuery(SymbolQuery):
    """期权数据领域基类。"""


@dataclass(frozen=True, slots=True)
class OptionsListQuery(OptionsQuery):
    capability: str = "options_list"


@dataclass(frozen=True, slots=True)
class OptionsTrendsQuery(OptionsQuery):
    capability: str = "options_trends"


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
    capability: str = "fund_flow"
    symbol: str = ""


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
    capability: str = "wencai"
    question: str = ""

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValidationError("wencai 查询必须提供 question", context={"capability": "wencai"})


@dataclass(frozen=True, slots=True)
class ScreeningQuery(SearchQuery):
    capability: str = "screening"
    condition: str = ""

    def __post_init__(self) -> None:
        if not self.condition.strip():
            raise ValidationError("screening 查询必须提供 condition", context={"capability": "screening"})


@dataclass(frozen=True, slots=True)
class SuggestQuery(SearchQuery):
    capability: str = "suggest"
    keyword: str = ""

    def __post_init__(self) -> None:
        if not self.keyword.strip():
            raise ValidationError("suggest 查询必须提供 keyword", context={"capability": "suggest"})


@dataclass(frozen=True, slots=True)
class IndexConstituentsQuery(SearchQuery):
    capability: str = "index_constituents"
    index_code: str = ""

    def __post_init__(self) -> None:
        if not self.index_code.strip():
            raise ValidationError("index_constituents 查询必须提供 index_code", context={"capability": "index_constituents"})


@dataclass(frozen=True, slots=True)
class IndustryBoardQuery(SearchQuery):
    capability: str = "industry_board"


@dataclass(frozen=True, slots=True)
class BoardListQuery(SearchQuery):
    capability: str = "board_list"


@dataclass(frozen=True, slots=True)
class BoardMemberQuery(SearchQuery):
    capability: str = "board_member"
    board_id: str = ""

    def __post_init__(self) -> None:
        if not self.board_id.strip():
            raise ValidationError("board_member 查询必须提供 board_id", context={"capability": "board_member"})


@dataclass(frozen=True, slots=True)
class MacroQuery(CapabilityQuery):
    """宏观/全球数据领域基类。"""


@dataclass(frozen=True, slots=True)
class FxRatesQuery(MacroQuery):
    capability: str = "fx_rates"


@dataclass(frozen=True, slots=True)
class GlobalQuotesQuery(MacroQuery):
    capability: str = "global_quotes"
