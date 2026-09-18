from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.execution.semantic import SemanticExecutionAdapter
from tstdx.provider import TdxProvider, WebProvider
from tstdx.runtime import QueryRequest, Runtime, request_from_typed
from tstdx.typed_query import (
    BalanceSheetQuery,
    CashFlowQuery,
    F10Query,
    FundHoldingsQuery,
    FundRankQuery,
    IncomeStatementQuery,
    NewsQuery,
)


class EastmoneySource:
    def __init__(self) -> None:
        self.calls = 0

    def fund_holdings(self, symbol: str, *, quarter: str = ""):
        self.calls += 1
        return {"symbol": symbol, "quarter": quarter}

    def balance_sheet(self, symbol: str, **kwargs):
        return {"kind": "balance_sheet", "symbol": symbol, **kwargs}

    def income_sheet(self, symbol: str, **kwargs):
        return {"kind": "income_sheet", "symbol": symbol, **kwargs}

    def cash_flow(self, symbol: str, **kwargs):
        return {"kind": "cash_flow", "symbol": symbol, **kwargs}

    def fund_rank(self, *, fund_type: int = 0, **kwargs):
        return {"kind": "fund_rank", "fund_type": fund_type, **kwargs}

    def news_financial(self, *, page: int = 1, size: int = 30):
        return {"kind": "news_financial", "page": page, "size": size}


class TdxSource:
    def f10(self, symbol: str, *, section: str = ""):
        return {"symbol": symbol, "section": section}


@pytest.mark.parametrize(
    ("capability", "provider", "channel", "params"),
    [
        ("fund_holdings", "eastmoney", "fund", {"symbol": "600519.SH"}),
        ("fund_rank", "eastmoney", "fund", {"fund_type": 0}),
        ("bond_kline", "eastmoney", "derivatives", {"symbol": "113001.SH"}),
        ("futures_kline", "eastmoney", "derivatives", {"symbol": "IF2509"}),
        ("options_snapshot", "eastmoney", "options", {"symbol": "10000001"}),
        ("research_reports", "eastmoney", "research", {"symbol": "600519.SH"}),
        ("balance_sheet", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("income_sheet", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("cash_flow", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("news_financial", "eastmoney", "news", {"page": 1, "size": 30}),
        ("f10", "tdx", "f10", {"symbol": "600519.SH"}),
    ],
)
def test_canonical_typed_capabilities_compile_through_query_planner(
    capability: str,
    provider: str,
    channel: str,
    params: dict[str, object],
) -> None:
    adapter = SemanticExecutionAdapter()
    request = QueryRequest(capability, params)

    plan = adapter.compile(request, provider)

    assert plan.provider == provider
    assert plan.channel == channel
    assert plan.spec.capability == capability
    assert plan.fingerprint.value.startswith("q1:")


def test_execute_typed_fund_holdings_uses_canonical_semantics() -> None:
    source = EastmoneySource()
    runtime = Runtime(provider_order=("eastmoney",))
    runtime.register_provider(WebProvider("eastmoney", source))
    query = FundHoldingsQuery(
        provider="eastmoney",
        symbol="600519.SH",
        options={"quarter": "2026Q2"},
    )

    response = runtime.execute_typed(query)

    assert response.success is True
    assert response.data == {"symbol": "600519.SH", "quarter": "2026Q2"}
    assert response.metadata["provider"] == "eastmoney"
    assert response.metadata["channel"] == "fund"
    assert response.metadata["provenance"]["capability"] == "fund_holdings"
    assert response.metadata["query_fingerprint"].startswith("q1:")


def test_registered_statement_queries_execute_through_datacenter_channel() -> None:
    runtime = Runtime(provider_order=("eastmoney",))
    runtime.register_provider(WebProvider("eastmoney", EastmoneySource()))
    queries = (
        BalanceSheetQuery(
            provider="eastmoney",
            symbol="600519.SH",
            options={"report_date": "2026-06-30", "size": 10},
        ),
        IncomeStatementQuery(provider="eastmoney", symbol="600519.SH"),
        CashFlowQuery(provider="eastmoney", symbol="600519.SH"),
    )

    responses = [runtime.execute_typed(query) for query in queries]

    assert all(response.success for response in responses)
    assert all(response.metadata["channel"] == "datacenter" for response in responses)
    assert [response.metadata["provenance"]["capability"] for response in responses] == [
        "balance_sheet",
        "income_sheet",
        "cash_flow",
    ]


def test_fund_rank_and_news_defaults_match_source_contracts() -> None:
    runtime = Runtime(provider_order=("eastmoney",))
    runtime.register_provider(WebProvider("eastmoney", EastmoneySource()))

    rank = runtime.execute_typed(FundRankQuery(provider="eastmoney"))
    news = runtime.execute_typed(NewsQuery(provider="eastmoney"))

    assert rank.success is True
    assert rank.data == {"kind": "fund_rank", "fund_type": 0}
    assert rank.metadata["channel"] == "fund"
    assert news.success is True
    assert news.data == {"kind": "news_financial", "page": 1, "size": 30}
    assert news.metadata["channel"] == "news"


def test_runtime_policy_prefilters_statically_unsupported_typed_providers() -> None:
    source = EastmoneySource()
    runtime = Runtime(provider_order=("tdx", "eastmoney"))
    runtime.register_provider(TdxProvider(TdxSource()))
    runtime.register_provider(WebProvider("eastmoney", source))

    response = runtime.execute_typed(FundHoldingsQuery(symbol="600519.SH"))

    assert response.success is True
    assert response.metadata["provider"] == "eastmoney"
    assert response.metadata["provider_attempts"] == [
        {"provider": "eastmoney", "status": "selected"}
    ]
    assert response.metadata["provenance"]["requested_provider"] == "eastmoney"
    assert response.metadata["provenance"]["fallback"] is False


def test_typed_options_participate_in_fingerprint() -> None:
    source = EastmoneySource()
    runtime = Runtime(provider_order=("eastmoney",))
    runtime.register_provider(WebProvider("eastmoney", source))
    q1 = FundHoldingsQuery(
        provider="eastmoney",
        symbol="600519.SH",
        options={"quarter": "2026Q1"},
    )
    q2 = FundHoldingsQuery(
        provider="eastmoney",
        symbol="600519.SH",
        options={"quarter": "2026Q2"},
    )

    first = runtime.execute_typed(q1)
    repeat = runtime.execute_typed(q1)
    different = runtime.execute_typed(q2)

    assert first.success is True
    assert repeat.success is True
    assert different.success is True
    assert source.calls == 3
    assert repeat.metadata["query_fingerprint"] == first.metadata["query_fingerprint"]
    assert repeat.metadata["provenance"]["cache_tier"] is None
    assert different.metadata["query_fingerprint"] != first.metadata["query_fingerprint"]


def test_execute_typed_f10_uses_tdx_channel() -> None:
    runtime = Runtime(provider_order=("tdx",))
    runtime.register_provider(TdxProvider(TdxSource()))
    query = F10Query(provider="tdx", symbol="600519.SH", section="股东研究")

    response = runtime.execute_typed(query)

    assert response.success is True
    assert response.data == {"symbol": "600519.SH", "section": "股东研究"}
    assert response.metadata["provider"] == "tdx"
    assert response.metadata["channel"] == "f10"
    assert response.metadata["provenance"]["capability"] == "f10"


def test_typed_provider_aliases_normalize_before_conflict_check() -> None:
    query = FundHoldingsQuery(provider="eastmoney", symbol="600519.SH")

    request = request_from_typed(query, metadata={"provider": "em"})

    assert request.metadata["provider"] == "eastmoney"


def test_typed_provider_conflict_is_rejected_before_execution() -> None:
    query = FundHoldingsQuery(provider="eastmoney", symbol="600519.SH")

    with pytest.raises(ValidationError, match="conflicts"):
        request_from_typed(query, metadata={"provider": "tdx"})
