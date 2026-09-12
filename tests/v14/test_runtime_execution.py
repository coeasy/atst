from __future__ import annotations

import pytest

from tstdx.execution import ExecutionGraph, ExecutionNode
from tstdx.provider import Provider
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


def test_runtime_honors_explicit_provider() -> None:
    runtime = Runtime()
    runtime.register_provider(_HealthyProvider())

    response = runtime.execute(
        QueryRequest("bars", {"symbol": "sh600519"}, metadata={"provider": "good"})
    )

    assert response.success is True
    assert response.metadata["provider"] == "good"


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
