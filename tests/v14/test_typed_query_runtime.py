"""Typed query contracts compiled through the single zero-cache kernel.

v16 clean-break: the v14 envelope (``Runtime`` / ``QueryRequest`` /
``SemanticExecutionAdapter``) is physically removed. ``Client.typed`` is the
only typed entry; these tests pin plan compilation, payload mapping,
fingerprint identity and Domain Record normalization against the kernel.
"""

from __future__ import annotations

from typing import Any

import pytest

from tstdx.client.api import Client
from tstdx.errors import ValidationError
from tstdx.query import QueryPlan, QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.runtime import UnifiedRuntime
from tstdx.typed_query import (
    BalanceSheetQuery,
    CashFlowQuery,
    F10Query,
    FundHoldingsQuery,
    FundRankQuery,
    IncomeStatementQuery,
    NewsQuery,
    TypedQueryResult,
)


class RecordingKernelExecutor:
    """Test-only ``KernelExecutor``: capture the compiled plan, return canned data."""

    def __init__(self, data: Any = None) -> None:
        self.plans: list[QueryPlan] = []
        self.data = data

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        self.plans.append(plan)
        return QueryResult.from_plan(self.data, plan=plan, provenance=Provenance.direct(plan))


def _client(data: Any = None) -> tuple[Client, RecordingKernelExecutor]:
    executor = RecordingKernelExecutor(data)
    return Client(runtime=UnifiedRuntime(executor=executor)), executor


@pytest.mark.parametrize(
    ("capability", "provider", "channel", "params"),
    [
        ("fund_holdings", "eastmoney", "fund", {"code": "000001"}),
        ("fund_rank", "eastmoney", "fund", {"fund_type": 0}),
        ("bond_kline", "eastmoney", "derivatives", {"code": "113001"}),
        ("futures_kline", "eastmoney", "derivatives", {"quote_id": "IF2509"}),
        ("options_snapshot", "eastmoney", "options", {"quote_id": "10000001"}),
        ("research_reports", "eastmoney", "research", {"symbol": "600519.SH"}),
        ("balance_sheet", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("income_sheet", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("cash_flow", "eastmoney", "datacenter", {"symbol": "600519.SH"}),
        ("news_financial", "eastmoney", "news", {"page": 1, "size": 30}),
        ("f10", "tdx", "f10", {"symbol": "600519.SH", "filename": "xxg.txt"}),
    ],
)
def test_canonical_typed_capabilities_compile_through_query_planner(
    capability: str,
    provider: str,
    channel: str,
    params: dict[str, object],
) -> None:
    spec = QuerySpec.build(
        capability,
        provider=provider,
        options={"args": [], "kwargs": dict(params)},
    )

    plan = QueryPlanner().compile(spec)

    assert plan.provider == provider
    assert plan.channel == channel
    assert plan.spec.capability == capability
    assert plan.fingerprint.value.startswith("q1:")


def test_typed_fund_holdings_compiles_canonical_semantics() -> None:
    client, executor = _client({"code": "000001", "dates": ["2026Q2"]})
    query = FundHoldingsQuery(
        provider="eastmoney",
        code="000001",
        options={"dates": ["2026Q2"]},
    )

    result = client.typed(query)

    plan = executor.plans[0]
    assert plan.provider == "eastmoney"
    assert plan.channel == "fund"
    assert plan.spec.capability == "fund_holdings"
    assert plan.spec.options["kwargs"] == {"code": "000001", "dates": ["2026Q2"]}
    assert plan.fingerprint.value.startswith("q1:")
    assert isinstance(result, TypedQueryResult)
    assert result.capability == "fund_holdings"


def test_registered_statement_queries_compile_through_datacenter_channel() -> None:
    client, executor = _client({"rows": []})
    queries = (
        BalanceSheetQuery(
            provider="eastmoney",
            symbol="600519.SH",
            options={"report_date": "2026-06-30", "size": 10},
        ),
        IncomeStatementQuery(provider="eastmoney", symbol="600519.SH"),
        CashFlowQuery(provider="eastmoney", symbol="600519.SH"),
    )

    results = [client.typed(query) for query in queries]

    plans = executor.plans
    assert [plan.channel for plan in plans] == ["datacenter"] * 3
    assert [plan.spec.capability for plan in plans] == [
        "balance_sheet",
        "income_sheet",
        "cash_flow",
    ]
    assert [result.capability for result in results] == [
        "balance_sheet",
        "income_sheet",
        "cash_flow",
    ]
    assert plans[0].spec.options["kwargs"] == {
        "symbol": "600519.SH",
        "report_date": "2026-06-30",
        "size": 10,
    }


def test_fund_rank_and_news_defaults_reach_the_compiled_plan() -> None:
    client, executor = _client({"rows": []})

    client.typed(FundRankQuery(provider="eastmoney"))
    client.typed(NewsQuery(provider="eastmoney"))

    rank_plan, news_plan = executor.plans
    assert (rank_plan.provider, rank_plan.channel) == ("eastmoney", "fund")
    assert rank_plan.spec.options["kwargs"] == {"fund_type": 0}
    assert (news_plan.provider, news_plan.channel) == ("eastmoney", "news")
    assert news_plan.spec.options["kwargs"] == {"page": 1, "size": 30}


def test_unregistered_provider_defaults_route_through_registry() -> None:
    client, executor = _client({})

    client.typed(FundHoldingsQuery(code="000001"))

    plan = executor.plans[0]
    # default_provider_for 优先注册表内的 composite/首方通道，路由完全由注册表决定。
    assert plan.provider == "derived"
    assert plan.channel == "catalog"
    assert plan.spec.capability == "fund_holdings"


def test_typed_options_participate_in_fingerprint_identity() -> None:
    client, executor = _client({})
    q1 = FundHoldingsQuery(provider="eastmoney", code="000001", options={"dates": ["2026Q1"]})
    q2 = FundHoldingsQuery(provider="eastmoney", code="000001", options={"dates": ["2026Q2"]})

    client.typed(q1)
    client.typed(q1)
    client.typed(q2)

    first, repeat, different = executor.plans
    # 零缓存内核：每一次 typed 调用都直达 executor，没有任何结果合并/复用。
    assert len(executor.plans) == 3
    assert repeat.fingerprint.value == first.fingerprint.value
    assert different.fingerprint.value != first.fingerprint.value


def test_typed_f10_compiles_on_tdx_channel() -> None:
    client, executor = _client([])

    client.typed(F10Query(provider="tdx", symbol="600519.SH", filename="zhuyao.txt"))

    plan = executor.plans[0]
    assert plan.provider == "tdx"
    assert plan.channel == "f10"
    assert plan.spec.options["kwargs"] == {"symbol": "600519.SH", "filename": "zhuyao.txt"}


def test_typed_provider_aliases_normalize_before_planning() -> None:
    client, executor = _client({})

    client.typed(FundHoldingsQuery(provider="em", code="000001"))

    assert executor.plans[0].provider == "eastmoney"


def test_typed_rejects_capability_missing_from_registry_before_execution() -> None:
    from tstdx.typed_query import CapabilityQuery

    client, executor = _client({})

    with pytest.raises(ValidationError, match="not registered"):
        client.typed(CapabilityQuery(capability="no_such_capability"))
    assert executor.plans == []
