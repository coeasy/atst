from __future__ import annotations

from tstdx.execution.semantic import SemanticExecutionAdapter
from tstdx.provider import TdxProvider, WebProvider
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.runtime import QueryRequest, Runtime


class CountingBarsClient:
    def __init__(self) -> None:
        self.calls = 0

    def bars(self, symbol, *, period="day", count=320):
        self.calls += 1
        return [{"symbol": symbol, "period": period, "count": count}]


class BarsOnlyClient:
    def bars(self, symbol, *, period="day", count=320):
        return [{"symbol": symbol, "period": period, "count": count}]


class QuotesSource:
    def quotes(self, symbols):
        return [{"symbol": item} for item in symbols]


def test_semantic_bridge_bars_plan_matches_direct_query_planner() -> None:
    request = QueryRequest(
        "bars",
        {"period": "day", "count": 20, "start": 3},
        metadata={"currentness": "historical", "max_age": 60.0},
        args=("600519.SH",),
    )
    adapter = SemanticExecutionAdapter()

    actual = adapter.compile(request, "tdx")
    expected = QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols="600519.SH",
            provider="tdx",
            period="day",
            count=20,
            start=3,
            currentness="historical",
            max_age=60.0,
        )
    )

    assert actual == expected
    assert actual.fingerprint.value == expected.fingerprint.value


def test_semantic_bridge_quotes_plan_matches_direct_query_planner() -> None:
    request = QueryRequest(
        "quotes",
        {"allow_partial": True},
        metadata={"currentness": "live", "deadline_ms": 800},
        args=(["sh600519", "sz000001"],),
    )
    adapter = SemanticExecutionAdapter()

    actual = adapter.compile(request, "tencent")
    expected = QueryPlanner().compile(
        QuerySpec.build(
            "quotes",
            symbols=["sh600519", "sz000001"],
            provider="tencent",
            allow_partial=True,
            currentness="live",
            deadline_ms=800,
        )
    )

    assert actual == expected
    assert actual.fingerprint.value == expected.fingerprint.value


def test_core_bars_use_canonical_query_provenance_and_zero_cache() -> None:
    client = CountingBarsClient()
    runtime = Runtime(provider_order=("tdx",))
    runtime.register_provider(TdxProvider(client))
    request = QueryRequest(
        "bars",
        {"period": "day", "count": 2},
        args=("600519.SH",),
    )

    first = runtime.execute(request)
    second = runtime.execute(request)

    assert first.success is True
    assert second.success is True
    assert client.calls == 2
    assert first.metadata["channel"] == "quotation"
    assert first.metadata["query_fingerprint"].startswith("q1:")
    first_provenance = first.metadata["provenance"]
    assert first_provenance["provider"] == "tdx"
    assert first_provenance["channel"] == "quotation"
    assert first_provenance["capability"] == "bars"
    assert first_provenance["kind"] == "direct"
    assert first_provenance["observed_at_ns"] > 0
    assert first_provenance["provider_timestamp"] is None
    assert first_provenance["cache_tier"] is None
    assert first_provenance["requested_provider"] == "tdx"
    assert first_provenance["fallback"] is False
    assert second.metadata["query_fingerprint"] == first.metadata["query_fingerprint"]
    assert second.metadata["provenance"]["provider"] == "tdx"
    assert second.metadata["provenance"]["kind"] == "direct"
    assert second.metadata["provenance"]["cache_tier"] is None


def test_runtime_never_switches_provider_privately() -> None:
    """v13 constitution: a failing first candidate is not retried elsewhere.

    Cross-Provider fallback requires an explicit ``FallbackPolicy`` via the
    orchestrator; the semantic adapter executes exactly one Provider.
    """
    runtime = Runtime(provider_order=("tdx", "tencent"))
    runtime.register_provider(TdxProvider(BarsOnlyClient()))
    runtime.register_provider(WebProvider("tencent", QuotesSource()))

    response = runtime.execute(QueryRequest("quotes", args=(["sh600519"],)))

    assert response.success is False
    assert response.metadata["provider_attempts"] == [
        {"provider": "tdx", "status": "unsupported", "detail": "quotes"}
    ]


def test_caller_provider_order_executes_first_provider() -> None:
    runtime = Runtime()
    runtime.register_provider(TdxProvider(BarsOnlyClient()))
    runtime.register_provider(WebProvider("tencent", QuotesSource()))
    request = QueryRequest(
        "quotes",
        args=(["sh600519"],),
        metadata={"providers": ("tencent", "tdx")},
    )

    response = runtime.execute(request)

    assert response.success is True
    assert response.metadata["provider"] == "tencent"
    assert response.metadata["provenance"]["provider"] == "tencent"
    assert response.metadata["provenance"]["requested_provider"] == "tencent"
    assert response.metadata["provenance"]["fallback"] is False
