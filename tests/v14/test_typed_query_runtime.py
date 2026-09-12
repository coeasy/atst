from __future__ import annotations

import pytest

from tstdx.cache_semantic import SemanticResultCache
from tstdx.errors import ValidationError
from tstdx.execution.semantic import SemanticExecutionAdapter
from tstdx.provider import TdxProvider, WebProvider
from tstdx.runtime import QueryRequest, Runtime, request_from_typed
from tstdx.typed_query import (
    BalanceSheetQuery,
    F10Query,
    FundHoldingsQuery,
)


class EastmoneySource:
    def __init__(self) -> None:
        self.calls = 0

    def fund_holdings(self, symbol: str, *, quarter: str = ""):
        self.calls += 1
        return {"symbol": symbol, "quarter": quarter}


class TdxSource:
    def f10(self, symbol: str, *, section: str = ""):
        return {"symbol": symbol, "section": section}


@pytest.mark.parametrize(
    ("capability", "provider", "channel"),
    [
        ("fund_holdings", "eastmoney", "fund"),
        ("bond_kline", "eastmoney", "derivatives"),
        ("futures_kline", "eastmoney", "derivatives"),
        ("options_snapshot", "eastmoney", "options"),
        ("research_reports", "eastmoney", "research"),
        ("f10", "tdx", "f10"),
    ],
)
def test_canonical_typed_capabilities_compile_through_query_planner(
    capability: str,
    provider: str,
    channel: str,
) -> None:
    adapter = SemanticExecutionAdapter()
    request = QueryRequest(capability, {"symbol": "600519.SH"})

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


def test_typed_options_participate_in_fingerprint_and_cache_identity() -> None:
    source = EastmoneySource()
    runtime = Runtime(
        provider_order=("eastmoney",),
        semantic_cache=SemanticResultCache(tier="l1"),
        default_cache_ttl=60.0,
    )
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
    cached = runtime.execute_typed(q1)
    different = runtime.execute_typed(q2)

    assert first.success is True
    assert cached.success is True
    assert different.success is True
    assert source.calls == 2
    assert cached.metadata["query_fingerprint"] == first.metadata["query_fingerprint"]
    assert cached.metadata["provenance"]["cache_tier"] == "l1"
    assert different.metadata["query_fingerprint"] != first.metadata["query_fingerprint"]
    assert different.metadata["provenance"]["cache_tier"] is None


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


def test_pending_typed_capability_cannot_bypass_provider_registry() -> None:
    with pytest.raises(ValidationError, match="not registered"):
        request_from_typed(BalanceSheetQuery(provider="eastmoney", symbol="600519.SH"))


def test_typed_provider_aliases_normalize_before_conflict_check() -> None:
    query = FundHoldingsQuery(provider="eastmoney", symbol="600519.SH")

    request = request_from_typed(query, metadata={"provider": "em"})

    assert request.metadata["provider"] == "eastmoney"


def test_typed_provider_conflict_is_rejected_before_execution() -> None:
    query = FundHoldingsQuery(provider="eastmoney", symbol="600519.SH")

    with pytest.raises(ValidationError, match="conflicts"):
        request_from_typed(query, metadata={"provider": "tdx"})
