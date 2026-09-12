from __future__ import annotations

import pytest

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
    def fund_holdings(self, symbol: str, *, quarter: str = ""):
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
    runtime = Runtime(provider_order=("eastmoney",))
    runtime.register_provider(WebProvider("eastmoney", EastmoneySource()))
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


def test_typed_provider_conflict_is_rejected_before_execution() -> None:
    query = FundHoldingsQuery(provider="eastmoney", symbol="600519.SH")

    with pytest.raises(ValidationError, match="conflicts"):
        request_from_typed(query, metadata={"provider": "tdx"})
