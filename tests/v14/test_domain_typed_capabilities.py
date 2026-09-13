from __future__ import annotations

import dataclasses
import importlib

import pytest

from tstdx.errors import ValidationError
from tstdx.runtime import Runtime, request_from_typed
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

    def test_all_domain_queries_compile_through_typed_adapter(self) -> None:
        """每个领域查询可通过 request_from_typed 生成 QueryRequest。"""
        from tstdx.runtime import QueryRequest

        for capability, cls in DOMAIN_CAPABILITIES.items():
            try:
                instance = cls()
            except ValidationError:
                # 有必填字段的领域查询：提供最小合法值
                instance = _minimal_instance(cls)
            request = request_from_typed(instance)
            assert isinstance(request, QueryRequest)
            assert request.operation == capability

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
        for capability, cls in DOMAIN_CAPABILITIES.items():
            assert dataclasses.is_dataclass(cls)
            assert cls.__dataclass_params__.frozen is True


class TestDomainFieldValidation:
    def test_wencai_requires_question(self) -> None:
        with pytest.raises(ValidationError):
            WencaiQuery()

    def test_screening_requires_condition(self) -> None:
        with pytest.raises(ValidationError):
            ScreeningQuery()

    def test_suggest_requires_keyword(self) -> None:
        with pytest.raises(ValidationError):
            SuggestQuery()

    def test_index_constituents_requires_index_code(self) -> None:
        with pytest.raises(ValidationError):
            IndexConstituentsQuery()

    def test_board_member_requires_board_id(self) -> None:
        with pytest.raises(ValidationError):
            BoardMemberQuery()

    def test_fund_multi_requires_codes(self) -> None:
        with pytest.raises(ValidationError):
            FundBaseInfoMultiQuery()


class TestDomainQueryExecution:
    def test_execute_typed_wencai_routes_to_iwencai(self) -> None:
        runtime = Runtime(provider_order=("iwencai",))
        source = _ScreeningSource()
        runtime.register_provider(_new_web_provider("iwencai", source))

        response = runtime.execute_typed(WencaiQuery(question="静态市盈率小于10"))

        assert response.success is True
        assert response.metadata["provider"] == "iwencai"
        assert response.metadata["channel"] == "screening"
        assert response.metadata["provenance"]["capability"] == "wencai"

    def test_execute_typed_fx_rates_routes_to_boc(self) -> None:
        runtime = Runtime(provider_order=("boc",))
        runtime.register_provider(_new_web_provider("boc", _FxSource()))

        response = runtime.execute_typed(FxRatesQuery())

        assert response.success is True
        assert response.metadata["provider"] == "boc"
        assert response.metadata["channel"] == "fx"
        assert response.metadata["provenance"]["capability"] == "fx_rates"

    def test_execute_typed_fund_manager_routes_to_eastmoney(self) -> None:
        runtime = Runtime(provider_order=("eastmoney",))
        runtime.register_provider(_new_web_provider("eastmoney", _EaseSource()))

        response = runtime.execute_typed(FundManagerQuery(code="000001"))

        assert response.success is True
        assert response.metadata["provider"] == "eastmoney"
        assert response.metadata["channel"] == "fund"
        assert response.data == {"code": "000001", "kind": "fund_manager"}

    def test_execute_typed_global_quotes_routes_to_tencent(self) -> None:
        runtime = Runtime(provider_order=("tencent",))
        runtime.register_provider(_new_web_provider("tencent", _TencentSource()))

        response = runtime.execute_typed(GlobalQuotesQuery())

        assert response.success is True
        assert response.metadata["provider"] == "tencent"
        assert response.data == {"kind": "global_quotes"}

    def test_execute_typed_convertible_bond_routes_to_jsl(self) -> None:
        runtime = Runtime(provider_order=("jsl",))
        runtime.register_provider(_new_web_provider("jsl", _JslSource()))

        response = runtime.execute_typed(ConvertibleBondQuery(symbol="sh113001"))

        assert response.success is True
        assert response.metadata["provider"] == "jsl"
        assert response.metadata["channel"] == "bond"
        assert response.data == {"kind": "convertible_bond", "symbol": "sh113001"}

    def test_execute_typed_financial_queries_route_by_registry(self) -> None:
        """财务类查询按注册表路由到各自的实际通道。"""
        from tstdx.provider import TdxProvider

        runtime = Runtime(provider_order=("eastmoney", "tdx"))
        runtime.register_provider(_new_web_provider("eastmoney", _EaseSource()))
        runtime.register_provider(TdxProvider(_TdxFinanceSource()))

        responses = [
            runtime.execute_typed(FinancialAbstractQuery(symbol="600519.SH")),
            runtime.execute_typed(DividendHistoryQuery(symbol="600519.SH")),
            runtime.execute_typed(StockValuationQuery(symbol="600519.SH")),
            runtime.execute_typed(HolderChangesQuery(symbol="600519.SH")),
            runtime.execute_typed(CapitalChangesQuery(symbol="600519.SH")),
            runtime.execute_typed(CorporateActionQuery(symbol="600519.SH")),
        ]

        assert all(response.success for response in responses)
        channel_map = {
            response.metadata["provenance"]["capability"]: response.metadata["channel"]
            for response in responses
        }
        # datacenter 族（eastmoney）
        assert channel_map["financial_abstract"] == "datacenter"
        assert channel_map["dividend_history"] == "datacenter"
        assert channel_map["stock_valuation"] == "datacenter"
        assert channel_map["holder_changes"] == "datacenter"
        # 非 datacenter 族按注册表真实通道
        assert channel_map["capital_changes"] == "quotation"  # tdx
        assert channel_map["corporate_action"] == "corporate"  # eastmoney

    def test_fund_domain_cache_identity_distinguishes_codes(self) -> None:
        from tstdx.cache_semantic import SemanticResultCache

        source = _EaseSource()
        runtime = Runtime(
            provider_order=("eastmoney",),
            semantic_cache=SemanticResultCache(tier="l1"),
            default_cache_ttl=60.0,
        )
        runtime.register_provider(_new_web_provider("eastmoney", source))

        first = runtime.execute_typed(FundManagerQuery(code="000001"))
        cached = runtime.execute_typed(FundManagerQuery(code="000001"))
        other = runtime.execute_typed(FundManagerQuery(code="000002"))

        assert source.calls == 2  # 第二个命中缓存
        assert first.success is True and cached.success is True
        assert cached.metadata["provenance"]["cache_tier"] == "l1"
        assert cached.metadata["query_fingerprint"] == first.metadata["query_fingerprint"]
        assert other.metadata["query_fingerprint"] != first.metadata["query_fingerprint"]

    def test_execute_typed_market_data_routes_correct_channel(self) -> None:
        runtime = Runtime(provider_order=("eastmoney", "tencent"))
        runtime.register_provider(_new_web_provider("eastmoney", _EaseSource()))
        runtime.register_provider(
            _new_web_provider("tencent", _TencentMarketSource())
        )

        hot = runtime.execute_typed(HotRankQuery())
        board = runtime.execute_typed(BoardRankQuery())

        assert hot.success is True
        assert hot.metadata["provider"] == "eastmoney"
        assert hot.metadata["channel"] == "hot_rank"
        assert board.success is True
        assert board.metadata["provider"] == "tencent"
        assert board.metadata["channel"] == "board_rank"


def _minimal_instance(cls: type) -> object:
    """为带必填字段的领域查询构造最小合法实例。"""
    from tstdx.typed_query import (
        BoardMemberQuery,
        FundBaseInfoMultiQuery,
        IndexConstituentsQuery,
        ScreeningQuery,
        SuggestQuery,
        WencaiQuery,
    )

    if cls is WencaiQuery:
        return WencaiQuery(question="x")
    if cls is ScreeningQuery:
        return ScreeningQuery(condition="x")
    if cls is SuggestQuery:
        return SuggestQuery(keyword="x")
    if cls is IndexConstituentsQuery:
        return IndexConstituentsQuery(index_code="000300")
    if cls is BoardMemberQuery:
        return BoardMemberQuery(board_id="BK0475")
    if cls is FundBaseInfoMultiQuery:
        return FundBaseInfoMultiQuery(codes=("000001",))
    return cls()


def _new_web_provider(provider: str, source: object):
    from tstdx.provider import WebProvider

    return WebProvider(provider, source)


class _ScreeningSource:
    def wencai(self, question: str = "", **kwargs):
        return {"kind": "wencai", "question": question}

    def screening(self, condition: str = "", **kwargs):
        return {"kind": "screening", "condition": condition, **kwargs}


class _FxSource:
    def fx_rates(self, **kwargs):
        return {"kind": "fx_rates", **kwargs}


class _EaseSource:
    def __init__(self) -> None:
        self.calls = 0

    def _count(self) -> None:
        self.calls += 1

    def fund_manager(self, code: str = "", **kwargs):
        self._count()
        return {"kind": "fund_manager", "code": code, **kwargs}

    def fund_base_info(self, code: str = "", **kwargs):
        return {"kind": "fund_base_info", "code": code, **kwargs}

    def fund_asset_allocation(self, code: str = "", **kwargs):
        return {"kind": "fund_asset_allocation", "code": code, **kwargs}

    def fund_period_change(self, code: str = "", **kwargs):
        return {"kind": "fund_period_change", "code": code, **kwargs}

    def fund_industry_distribution(self, code: str = "", **kwargs):
        return {"kind": "fund_industry_distribution", "code": code, **kwargs}

    def fund_public_dates(self, code: str = "", **kwargs):
        return {"kind": "fund_public_dates", "code": code, **kwargs}

    def fund_base_info_multi(self, codes=(), **kwargs):
        return {"kind": "fund_base_info_multi", "codes": codes, **kwargs}

    def financial_abstract(self, symbol: str = "", **kwargs):
        return {"kind": "financial_abstract", "symbol": symbol, **kwargs}

    def dividend_history(self, symbol: str = "", **kwargs):
        return {"kind": "dividend_history", "symbol": symbol, **kwargs}

    def stock_valuation(self, symbol: str = "", **kwargs):
        return {"kind": "stock_valuation", "symbol": symbol, **kwargs}

    def holder_changes(self, symbol: str = "", **kwargs):
        return {"kind": "holder_changes", "symbol": symbol, **kwargs}

    def holder_num(self, symbol: str = "", **kwargs):
        return {"kind": "holder_num", "symbol": symbol, **kwargs}

    def free_holders(self, symbol: str = "", **kwargs):
        return {"kind": "free_holders", "symbol": symbol, **kwargs}

    def announcements(self, symbol: str = "", **kwargs):
        return {"kind": "announcements", "symbol": symbol, **kwargs}

    def ipo_review(self, symbol: str = "", **kwargs):
        return {"kind": "ipo_review", "symbol": symbol, **kwargs}

    def capital_changes(self, symbol: str = "", **kwargs):
        return {"kind": "capital_changes", "symbol": symbol, **kwargs}

    def corporate_action(self, symbol: str = "", **kwargs):
        return {"kind": "corporate_action", "symbol": symbol, **kwargs}

    def bond_base_info(self, symbol: str = "", **kwargs):
        return {"kind": "bond_base_info", "symbol": symbol, **kwargs}

    def bond_all_base_info(self, symbol: str = "", **kwargs):
        return {"kind": "bond_all_base_info", "symbol": symbol, **kwargs}

    def bond_realtime(self, symbol: str = "", **kwargs):
        return {"kind": "bond_realtime", "symbol": symbol, **kwargs}

    def bond_trades(self, symbol: str = "", **kwargs):
        return {"kind": "bond_trades", "symbol": symbol, **kwargs}

    def bond_today_bill(self, symbol: str = "", **kwargs):
        return {"kind": "bond_today_bill", "symbol": symbol, **kwargs}

    def bond_history_bill(self, symbol: str = "", **kwargs):
        return {"kind": "bond_history_bill", "symbol": symbol, **kwargs}

    def futures_base_info(self, symbol: str = "", **kwargs):
        return {"kind": "futures_base_info", "symbol": symbol, **kwargs}

    def futures_realtime(self, symbol: str = "", **kwargs):
        return {"kind": "futures_realtime", "symbol": symbol, **kwargs}

    def futures_trades(self, symbol: str = "", **kwargs):
        return {"kind": "futures_trades", "symbol": symbol, **kwargs}

    def options_list(self, symbol: str = "", **kwargs):
        return {"kind": "options_list", "symbol": symbol, **kwargs}

    def options_trends(self, symbol: str = "", **kwargs):
        return {"kind": "options_trends", "symbol": symbol, **kwargs}

    def research_visits(self, symbol: str = "", **kwargs):
        return {"kind": "research_visits", "symbol": symbol, **kwargs}

    def hot_rank(self, **kwargs):
        return {"kind": "hot_rank", **kwargs}

    def limit_pool(self, **kwargs):
        return {"kind": "limit_pool", **kwargs}

    def northbound(self, **kwargs):
        return {"kind": "northbound", **kwargs}

    def longhu(self, symbol: str = "", **kwargs):
        return {"kind": "longhu", "symbol": symbol, **kwargs}

    def market_stat(self, **kwargs):
        return {"kind": "market_stat", **kwargs}

    def fund_flow(self, symbol: str = "", **kwargs):
        return {"kind": "fund_flow", "symbol": symbol, **kwargs}

    def stock_changes(self, **kwargs):
        return {"kind": "stock_changes", **kwargs}

    def rank(self, **kwargs):
        return {"kind": "rank", **kwargs}

    def index_constituents(self, index_code: str = "", **kwargs):
        return {"kind": "index_constituents", "index_code": index_code, **kwargs}


class _SinaMarketSource:
    def board_rank(self, **kwargs):
        return {"kind": "board_rank", **kwargs}

    def board_list(self, **kwargs):
        return {"kind": "board_list", **kwargs}

    def industry_board(self, **kwargs):
        return {"kind": "industry_board", **kwargs}

    def suggest(self, keyword: str = "", **kwargs):
        return {"kind": "suggest", "keyword": keyword, **kwargs}

    def fund_flow(self, symbol: str = "", **kwargs):
        return {"kind": "fund_flow", "symbol": symbol, **kwargs}


class _TdxFinanceSource:
    def capital_changes(self, symbol: str = "", **kwargs):
        return {"kind": "capital_changes", "symbol": symbol, **kwargs}


class _TencentMarketSource:
    def board_rank(self, **kwargs):
        return {"kind": "board_rank", **kwargs}

    def global_quotes(self, **kwargs):
        return {"kind": "global_quotes", **kwargs}

    def market_stat(self, **kwargs):
        return {"kind": "market_stat", **kwargs}


class _TencentSource:
    def global_quotes(self, **kwargs):
        return {"kind": "global_quotes", **kwargs}


class _JslSource:
    def convertible_bond(self, symbol: str = "", **kwargs):
        return {"kind": "convertible_bond", "symbol": symbol, **kwargs}