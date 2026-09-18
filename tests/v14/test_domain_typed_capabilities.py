"""Domain typed-query contracts executed through the single zero-cache kernel.

``Client.typed`` is the sole typed entry (v16 clean-break removed the v14
envelope). These tests pin: registry alignment, payload compilation, frozen
contracts, and per-capability Provider/Channel routing via an injected kernel
executor.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from tstdx.catalog.capability import default_provider_for
from tstdx.client_api import Client
from tstdx.errors import ValidationError
from tstdx.query import QueryPlan, QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.runtime import UnifiedRuntime
from tstdx.typed_query import (
    AnnouncementsQuery,
    BoardListQuery,
    BoardMemberQuery,
    BoardRankQuery,
    BondAllBaseInfoQuery,
    BondBaseInfoQuery,
    BondHistoryBillQuery,
    BondRealtimeQuery,
    BondTodayBillQuery,
    BondTradesQuery,
    CapitalChangesQuery,
    ConvertibleBondQuery,
    CorporateActionQuery,
    DividendHistoryQuery,
    FinancialAbstractQuery,
    FreeHoldersQuery,
    FundAssetAllocationQuery,
    FundBaseInfoMultiQuery,
    FundBaseInfoQuery,
    FundFlowQuery,
    FundIndustryDistributionQuery,
    FundManagerQuery,
    FundPeriodChangeQuery,
    FundPublicDatesQuery,
    FuturesBaseInfoQuery,
    FuturesRealtimeQuery,
    FuturesTradesQuery,
    FxRatesQuery,
    GlobalQuotesQuery,
    HolderChangesQuery,
    HolderNumQuery,
    HotRankQuery,
    IndexConstituentsQuery,
    IndustryBoardQuery,
    IpoReviewQuery,
    LimitPoolQuery,
    LonghuQuery,
    MarginQuery,
    MarketStatQuery,
    NorthboundQuery,
    OptionsListQuery,
    OptionsTrendsQuery,
    RankQuery,
    ResearchVisitsQuery,
    ScreeningQuery,
    StockChangesQuery,
    StockValuationQuery,
    SuggestQuery,
    WencaiQuery,
    call_payload_from_typed,
)

# 领域化契约的 capability -> 类映射（v14 Phase 1 扩展）
DOMAIN_CAPABILITIES: dict[str, type] = {
    # Financial
    "financial_abstract": FinancialAbstractQuery,
    "dividend_history": DividendHistoryQuery,
    "stock_valuation": StockValuationQuery,
    "holder_changes": HolderChangesQuery,
    "holder_num": HolderNumQuery,
    "free_holders": FreeHoldersQuery,
    "capital_changes": CapitalChangesQuery,
    "corporate_action": CorporateActionQuery,
    "announcements": AnnouncementsQuery,
    "ipo_review": IpoReviewQuery,
    # Fund
    "fund_base_info": FundBaseInfoQuery,
    "fund_base_info_multi": FundBaseInfoMultiQuery,
    "fund_manager": FundManagerQuery,
    "fund_asset_allocation": FundAssetAllocationQuery,
    "fund_period_change": FundPeriodChangeQuery,
    "fund_industry_distribution": FundIndustryDistributionQuery,
    "fund_public_dates": FundPublicDatesQuery,
    # Bond
    "bond_base_info": BondBaseInfoQuery,
    "bond_all_base_info": BondAllBaseInfoQuery,
    "bond_realtime": BondRealtimeQuery,
    "bond_trades": BondTradesQuery,
    "bond_today_bill": BondTodayBillQuery,
    "bond_history_bill": BondHistoryBillQuery,
    "convertible_bond": ConvertibleBondQuery,
    # Futures
    "futures_base_info": FuturesBaseInfoQuery,
    "futures_realtime": FuturesRealtimeQuery,
    "futures_trades": FuturesTradesQuery,
    # Options
    "options_list": OptionsListQuery,
    "options_trends": OptionsTrendsQuery,
    # Research
    "research_visits": ResearchVisitsQuery,
    # Market data
    "hot_rank": HotRankQuery,
    "limit_pool": LimitPoolQuery,
    "northbound": NorthboundQuery,
    "margin": MarginQuery,
    "longhu": LonghuQuery,
    "market_stat": MarketStatQuery,
    "board_rank": BoardRankQuery,
    "fund_flow": FundFlowQuery,
    "stock_changes": StockChangesQuery,
    "rank": RankQuery,
    # Search
    "wencai": WencaiQuery,
    "screening": ScreeningQuery,
    "suggest": SuggestQuery,
    "index_constituents": IndexConstituentsQuery,
    "industry_board": IndustryBoardQuery,
    "board_list": BoardListQuery,
    "board_member": BoardMemberQuery,
    # Macro / global
    "fx_rates": FxRatesQuery,
    "global_quotes": GlobalQuotesQuery,
}


class RecordingKernelExecutor:
    def __init__(self, data: Any = None) -> None:
        self.plans: list[QueryPlan] = []
        self.data = data

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        self.plans.append(plan)
        return QueryResult.from_plan(self.data, plan=plan, provenance=Provenance.direct(plan))


def _typed(query: Any, data: Any = None) -> tuple[QueryPlan, Any]:
    executor = RecordingKernelExecutor({} if data is None else data)
    client = Client(runtime=UnifiedRuntime(executor=executor))
    result = client.typed(query)
    return executor.plans[0], result


class TestDomainTypedCapabilities:
    def test_all_domain_capabilities_semantic_ready(self) -> None:
        from tstdx.providers import PROVIDERS

        ready = set()
        for pid in PROVIDERS.ids():
            ready |= set(PROVIDERS.get(pid).capabilities())

        for capability, cls in DOMAIN_CAPABILITIES.items():
            assert capability in ready, f"{capability} 未在 PROVIDERS 注册表"
            try:
                instance = cls()
            except ValidationError:
                # 带必填字段的领域查询：构造最小合法实例
                instance = _minimal_instance(cls)
            assert instance.semantic_ready, f"{capability} semantic_ready=False"
            assert instance.capability == capability

    def test_all_domain_queries_compile_through_kernel_planner(self) -> None:
        """每个领域查询可经 call_payload_from_typed + QueryPlanner 编译成 plan。"""
        for capability, cls in DOMAIN_CAPABILITIES.items():
            try:
                instance = cls()
            except ValidationError:
                instance = _minimal_instance(cls)
            payload = call_payload_from_typed(instance)
            spec = QuerySpec.build(
                capability,
                provider=instance.provider or default_provider_for(capability),
                options={"args": [], "kwargs": payload},
            )

            plan = QueryPlanner().compile(spec)

            assert plan.spec.capability == capability, capability
            assert plan.fingerprint.value.startswith("q1:")

    def test_domain_capability_matches_registry_channel(self) -> None:
        """验证每个领域 capability 在注册表中存在实际数据通道。"""
        from tstdx.providers import PROVIDERS

        for capability in DOMAIN_CAPABILITIES:
            channels = [
                pid
                for pid in PROVIDERS.ids()
                if PROVIDERS.supports(pid, capability)
            ]
            assert channels, f"{capability} 没有支持它的 Provider"

    def test_frozen_immutable_contracts(self) -> None:
        """Typed Query 契约为冻结 dataclass。"""
        for cls in DOMAIN_CAPABILITIES.values():
            assert dataclasses.is_dataclass(cls)
            assert cls.__dataclass_params__.frozen is True


class TestDomainFieldValidation:
    def test_wencai_requires_query(self) -> None:
        with pytest.raises(ValidationError):
            WencaiQuery()

    def test_screening_requires_query(self) -> None:
        with pytest.raises(ValidationError):
            ScreeningQuery()

    def test_suggest_requires_key(self) -> None:
        with pytest.raises(ValidationError):
            SuggestQuery()

    def test_index_constituents_requires_index(self) -> None:
        with pytest.raises(ValidationError):
            IndexConstituentsQuery()

    def test_board_member_requires_node(self) -> None:
        with pytest.raises(ValidationError):
            BoardMemberQuery()

    def test_fund_multi_requires_codes(self) -> None:
        with pytest.raises(ValidationError):
            FundBaseInfoMultiQuery()


class TestDomainQueryExecution:
    def test_typed_wencai_routes_to_iwencai(self) -> None:
        plan, result = _typed(WencaiQuery(provider="iwencai", query="静态市盈率小于10"))

        assert (plan.provider, plan.channel) == ("iwencai", "screening")
        assert plan.spec.capability == "wencai"
        assert result.capability == "wencai"

    def test_typed_fx_rates_routes_to_boc(self) -> None:
        plan, _ = _typed(FxRatesQuery(provider="boc"))

        assert (plan.provider, plan.channel) == ("boc", "fx")
        assert plan.spec.capability == "fx_rates"

    def test_typed_fund_manager_routes_to_eastmoney(self) -> None:
        plan, result = _typed(FundManagerQuery(provider="eastmoney", code="000001"))

        assert (plan.provider, plan.channel) == ("eastmoney", "fund")
        assert plan.spec.options["kwargs"] == {"code": "000001"}
        from tstdx.domain.records import FundRecord

        assert isinstance(result.data[0], FundRecord)

    def test_typed_global_quotes_routes_to_tencent(self) -> None:
        plan, _ = _typed(GlobalQuotesQuery(provider="tencent", symbols=("DJI", "IXIC")))

        assert (plan.provider, plan.channel) == ("tencent", "global")
        assert plan.spec.options["kwargs"] == {"symbols": ["DJI", "IXIC"]}

    def test_typed_convertible_bond_routes_to_jsl(self) -> None:
        plan, _ = _typed(ConvertibleBondQuery(provider="jsl", symbols=("sh113001",)))

        assert (plan.provider, plan.channel) == ("jsl", "bond")
        assert plan.spec.options["kwargs"] == {"symbols": ["sh113001"]}

    def test_typed_financial_queries_route_by_registry(self) -> None:
        """财务类查询按注册表路由到各自的实际通道。"""
        queries = (
            FinancialAbstractQuery(provider="eastmoney", symbol="600519.SH"),
            DividendHistoryQuery(provider="eastmoney", symbol="600519.SH"),
            StockValuationQuery(provider="eastmoney", symbol="600519.SH"),
            HolderChangesQuery(provider="eastmoney", symbol="600519.SH"),
            CapitalChangesQuery(provider="tdx", symbol="600519.SH"),
            CorporateActionQuery(provider="eastmoney", symbol="600519.SH"),
        )

        channel_map = {
            plan.spec.capability: (plan.provider, plan.channel)
            for plan in (_typed(query)[0] for query in queries)
        }
        # datacenter 族（eastmoney）
        assert channel_map["financial_abstract"] == ("eastmoney", "datacenter")
        assert channel_map["dividend_history"] == ("eastmoney", "datacenter")
        assert channel_map["stock_valuation"] == ("eastmoney", "datacenter")
        assert channel_map["holder_changes"] == ("eastmoney", "datacenter")
        # 非 datacenter 族按注册表真实通道
        assert channel_map["capital_changes"] == ("tdx", "quotation")
        assert channel_map["corporate_action"] == ("eastmoney", "corporate")

    def test_typed_market_data_routes_correct_channel(self) -> None:
        hot_plan, _ = _typed(HotRankQuery(provider="eastmoney"))
        board_plan, _ = _typed(BoardRankQuery(provider="tencent"))

        assert (hot_plan.provider, hot_plan.channel) == ("eastmoney", "hot_rank")
        assert (board_plan.provider, board_plan.channel) == ("tencent", "board_rank")


def _minimal_instance(cls: type) -> Any:
    """为带必填字段的领域查询构造最小合法实例。"""
    if cls is WencaiQuery:
        return WencaiQuery(query="x")
    if cls is ScreeningQuery:
        return ScreeningQuery(query="x")
    if cls is SuggestQuery:
        return SuggestQuery(key="x")
    if cls is IndexConstituentsQuery:
        return IndexConstituentsQuery(index="000300")
    if cls is BoardMemberQuery:
        return BoardMemberQuery(node="BK0475")
    if cls is FundBaseInfoMultiQuery:
        return FundBaseInfoMultiQuery(codes=("000001",))
    return cls()
