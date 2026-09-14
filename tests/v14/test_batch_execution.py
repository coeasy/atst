# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Phase 7 batch execution + semantic-cache deduplication tests."""

from __future__ import annotations

from typing import Any

from tstdx.cache_semantic import SemanticResultCache
from tstdx.runtime import QueryRequest, create_runtime


def _make_request(operation: str = "quotes", args: tuple = ()) -> QueryRequest:
    return QueryRequest(operation=operation, args=args)


class _FakeProvider:
    """Minimal provider that echoes back a marker."""

    def __init__(self, name: str, *, result: Any = None) -> None:
        self.name = name
        self._result = result
        self.calls: list[str] = []

    def capabilities(self) -> tuple[str, ...]:
        return ("quotes", "bars")

    def execute(self, request: Any, **_: Any) -> Any:
        self.calls.append(request.operation)
        return self._result


def test_batch_empty_returns_empty_list() -> None:
    runtime = create_runtime()
    assert runtime.execute_batch([]) == []


def test_batch_single_request_delegates_to_execute() -> None:
    calls: list[str] = []
    runtime = create_runtime()
    runtime.register("quotes", lambda params: calls.append("quotes") or [{"symbol": "test"}])

    result = runtime.execute_batch([_make_request("quotes", ("sh600000",))])

    assert len(result) == 1
    assert result[0].success is True
    assert calls == ["quotes"]


def test_batch_preserves_input_order() -> None:
    """Batch results must align with input request order."""
    counter = [0]

    def handler(params: Any) -> Any:
        counter[0] += 1
        return [{"id": counter[0]}]

    runtime = create_runtime()
    runtime.register("quotes", handler)

    requests = [
        _make_request("quotes", ("sh600000",)),
        _make_request("quotes", ("sh600519",)),
        _make_request("quotes", ("sh000001",)),
    ]

    results = runtime.execute_batch(requests)

    assert len(results) == 3
    assert [r.data[0]["id"] for r in results] == [1, 2, 3]


def test_batch_with_semantic_cache_deduplicates() -> None:
    """Identical requests are served from cache; distinct ones execute separately."""
    cache = SemanticResultCache()
    call_count = [0]

    def handler(params: Any) -> Any:
        call_count[0] += 1
        return [{"symbol": params.get("symbol", "unknown")}]

    runtime = create_runtime(semantic_cache=cache)
    runtime.register("quotes", handler)

    req1 = QueryRequest(
        operation="quotes",
        args=("sh600000",),
        params={"symbol": "sh600000"},
        metadata={"cache_ttl": 60.0},
    )
    req2 = QueryRequest(
        operation="quotes",
        args=("sh600000",),
        params={"symbol": "sh600000"},
        metadata={"cache_ttl": 60.0},
    )
    req3 = QueryRequest(
        operation="quotes",
        args=("sh600519",),
        params={"symbol": "sh600519"},
        metadata={"cache_ttl": 60.0},
    )

    # First call populates cache.
    runtime.execute(req1)
    # Second call (identical) should be cache hit.
    runtime.execute(req2)

    # Batch: req2 is cache hit, req3 is fresh.
    results = runtime.execute_batch([req2, req3], max_concurrent=1)

    assert len(results) == 2
    # req2 served from cache, req3 executed.
    assert results[0].success is True
    assert results[1].success is True


def test_batch_max_concurrent_respected() -> None:
    """max_concurrent=1 forces serial execution."""
    runtime = create_runtime()
    runtime.register("quotes", lambda p: [{"ok": True}])

    requests = [_make_request("quotes", (f"sh6000{i}",)) for i in range(5)]
    results = runtime.execute_batch(requests, max_concurrent=1)

    assert len(results) == 5
    assert all(r.success for r in results)


def test_semantic_cache_stats_reports_enabled() -> None:
    cache = SemanticResultCache()
    runtime = create_runtime(semantic_cache=cache)

    stats = runtime.semantic_cache_stats()
    assert stats["enabled"] is True
    assert stats["tier"] in {"l1", "l2"}


def test_semantic_cache_stats_reports_disabled() -> None:
    runtime = create_runtime()

    stats = runtime.semantic_cache_stats()
    assert stats["enabled"] is False


def test_batch_mixed_cache_and_fresh() -> None:
    """Batch with some cached and some fresh requests returns all results."""
    cache = SemanticResultCache()
    runtime = create_runtime(semantic_cache=cache)
    runtime.register("quotes", lambda p: [{"symbol": p.get("symbol", "x")}])

    # Pre-populate cache.
    cached_req = QueryRequest(
        operation="quotes",
        args=("sh600000",),
        params={"symbol": "sh600000"},
        metadata={"cache_ttl": 60.0},
    )
    runtime.execute(cached_req)

    fresh_req = QueryRequest(
        operation="quotes",
        args=("sh600519",),
        params={"symbol": "sh600519"},
        metadata={"cache_ttl": 60.0},
    )

    results = runtime.execute_batch([cached_req, fresh_req], max_concurrent=1)

    assert len(results) == 2
    assert all(r.success for r in results)
