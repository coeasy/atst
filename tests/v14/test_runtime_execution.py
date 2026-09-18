from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.execution import ExecutionGraph, ExecutionNode, ExecutionPlanner
from tstdx.provider import Provider, ProviderRouter, TdxProvider, WebProvider
from tstdx.runtime import QueryRequest, Runtime


class _FailingProvider(Provider):
    name = "bad"

    def query(self, request):
        raise RuntimeError("boom")


class _HealthyProvider(Provider):
    name = "good"

    def query(self, request):
        return {"operation": request.operation, "params": request.params}


class _UnhealthyProvider(Provider):
    name = "unhealthy"

    def health(self) -> bool:
        return False

    def query(self, request):  # pragma: no cover - health gate must prevent this
        raise AssertionError("unhealthy provider must not execute")


def test_runtime_falls_back_to_next_healthy_provider() -> None:
    runtime = Runtime(provider_order=("bad", "unhealthy", "good"))
    runtime.register_provider(_FailingProvider())
    runtime.register_provider(_UnhealthyProvider())
    runtime.register_provider(_HealthyProvider())

    response = runtime.execute(QueryRequest("quotes", {"symbols": ["sh600519"]}))

    assert response.success is True
    assert response.data == {
        "operation": "quotes",
        "params": {"symbols": ["sh600519"]},
    }
    assert response.metadata["provider"] == "good"
    assert response.metadata["execution"] == "execution-plan"
    assert response.metadata["request_id"]
    assert response.metadata["trace_id"]
    assert response.metadata["request_key"]
    assert [item["status"] for item in response.metadata["provider_attempts"]] == [
        "failed",
        "unhealthy",
        "selected",
    ]


def test_runtime_honors_explicit_provider() -> None:
    runtime = Runtime()
    runtime.register_provider(_HealthyProvider())

    response = runtime.execute(
        QueryRequest("bars", {"symbol": "sh600519"}, metadata={"provider": "good"})
    )

    assert response.success is True
    assert response.metadata["provider"] == "good"
    assert response.metadata["provider_attempts"] == [{"provider": "good", "status": "selected"}]


def test_runtime_preserves_compatibility_handlers() -> None:
    runtime = Runtime()
    runtime.register("echo", lambda params: params["value"])

    response = runtime.execute(QueryRequest("echo", {"value": 7}))

    assert response.success is True
    assert response.data == 7
    assert response.metadata["execution"] == "compat-handler"


def test_runtime_reports_unsupported_operation_without_backends() -> None:
    response = Runtime().execute(QueryRequest("missing"))

    assert response.success is False
    assert response.error == "unsupported operation: missing"


def test_runtime_reuses_router_owned_by_injected_planner() -> None:
    router = ProviderRouter()
    planner = ExecutionPlanner(router)
    runtime = Runtime(planner=planner)
    runtime.register_provider(_HealthyProvider())

    response = runtime.execute(QueryRequest("quotes"))

    assert response.success is True
    assert runtime.router is router
    assert response.metadata["provider"] == "good"


def test_runtime_rejects_mismatched_injected_router_and_planner() -> None:
    planner = ExecutionPlanner(ProviderRouter())

    with pytest.raises(ValueError, match="same provider router"):
        Runtime(router=ProviderRouter(), planner=planner)


def test_request_key_is_stable_but_is_not_semantic_cache_identity() -> None:
    first = QueryRequest("bars", {"count": 10}, {"trace": "a"}, ("sh600519",))
    second = QueryRequest("bars", {"count": 10}, {"trace": "b"}, ("sh600519",))
    different = QueryRequest("bars", {"count": 20}, {"trace": "a"}, ("sh600519",))

    assert first.request_key == second.request_key
    assert first.request_key != different.request_key


def test_tdx_provider_preserves_positional_and_keyword_arguments() -> None:
    class Client:
        def bars(self, symbol, *, count):
            return symbol, count

    runtime = Runtime(provider_order=("tdx",))
    runtime.register_provider(TdxProvider(Client()))

    response = runtime.execute(QueryRequest("bars", {"count": 5}, args=("sh600519",)))

    assert response.success is True
    assert response.data == ("sh600519", 5)


def test_runtime_does_not_retry_next_provider_when_first_is_unsupported() -> None:
    class BarsOnlyClient:
        def bars(self, symbol):
            return [symbol]

    class QuotesSource:
        def quotes(self, symbols):
            return list(symbols)

    runtime = Runtime(provider_order=("tdx", "tencent"))
    runtime.register_provider(TdxProvider(BarsOnlyClient()))
    runtime.register_provider(WebProvider("tencent", QuotesSource()))

    response = runtime.execute(QueryRequest("quotes", args=(["sh600519"],)))

    assert response.success is False
    assert response.metadata["provider_attempts"] == [
        {"provider": "tdx", "status": "unsupported", "detail": "quotes"}
    ]


def test_web_provider_rejects_ambiguous_web_identity() -> None:
    with pytest.raises(ValidationError, match="未知 provider"):
        WebProvider("web", object())


def test_explicit_provider_reports_unsupported_operation() -> None:
    class BarsOnlyClient:
        def bars(self, symbol):
            return [symbol]

    runtime = Runtime()
    runtime.register_provider(TdxProvider(BarsOnlyClient()))

    response = runtime.execute(
        QueryRequest("quotes", args=(["sh600519"],), metadata={"provider": "tdx"})
    )

    assert response.success is False
    assert response.metadata["error_type"] == "AttributeError"
    assert "does not support operation" in (response.error or "")
    assert response.metadata["provider_attempts"] == [
        {"provider": "tdx", "status": "unsupported", "detail": "quotes"}
    ]


def test_execution_graph_rejects_cycles() -> None:
    graph = ExecutionGraph()
    graph.add_node(ExecutionNode("a", lambda context, **inputs: None, ["b"]))
    graph.add_node(ExecutionNode("b", lambda context, **inputs: None, ["a"]))

    with pytest.raises(ValueError, match="cycle"):
        graph.resolve_order()


def test_execution_graph_rejects_missing_dependencies() -> None:
    graph = ExecutionGraph()
    graph.add_node(ExecutionNode("a", lambda context, **inputs: None, ["missing"]))

    with pytest.raises(ValueError, match="missing node"):
        graph.resolve_order()
