from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..cache_semantic import SemanticResultCache
from ..execution.planner import ExecutionPlanner
from ..execution.semantic import SemanticExecutionAdapter
from ..provider.base import Provider
from ..provider.router import ProviderRouter
from ..result import QueryResult
from .context import ExecutionContext
from .request import QueryRequest
from .response import QueryResponse


class Runtime:
    """V14 orchestration kernel entrypoint.

    ``QueryRequest`` is a boundary call envelope. Canonical market semantics are
    owned by :mod:`tstdx.query` / :mod:`tstdx.result`; this runtime coordinates
    execution adapters and DAG steps around those contracts.
    """

    def __init__(
        self,
        *,
        router: ProviderRouter | None = None,
        planner: ExecutionPlanner | None = None,
        provider_order: Sequence[str] | None = None,
        semantic_cache: SemanticResultCache | None = None,
        default_cache_ttl: float | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        if default_cache_ttl is not None and default_cache_ttl < 0:
            raise ValueError("default_cache_ttl must be >= 0 or None")

        if planner is None:
            self.router = router or ProviderRouter()
            semantic = SemanticExecutionAdapter(
                cache=semantic_cache,
                default_cache_ttl=default_cache_ttl,
            )
            self.planner = ExecutionPlanner(
                self.router,
                provider_order=provider_order,
                semantic=semantic,
            )
        else:
            if router is not None and planner.router is not None and planner.router is not router:
                raise ValueError("planner and runtime must use the same provider router")
            self.router = router or planner.router or ProviderRouter()
            if planner.router is None:
                planner.router = self.router
            if provider_order is not None:
                planner.provider_order = tuple(provider_order)
            if semantic_cache is not None:
                planner.semantic.cache = semantic_cache
            if default_cache_ttl is not None:
                planner.semantic.default_cache_ttl = default_cache_ttl
            self.planner = planner

    def register(self, operation: str, handler: Callable[..., Any]) -> None:
        if not operation:
            raise ValueError("operation must not be empty")
        self._handlers[operation] = handler

    def register_provider(self, provider: Provider) -> None:
        self.router.register(provider)

    @staticmethod
    def _execution_metadata(context: ExecutionContext) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if context.provider is not None:
            metadata["provider"] = context.provider
        attempts = context.metadata.get("provider_attempts")
        if isinstance(attempts, list):
            metadata["provider_attempts"] = [dict(item) for item in attempts]
        return metadata

    @staticmethod
    def _unwrap_result(result: Any) -> tuple[Any, dict[str, Any]]:
        if not isinstance(result, QueryResult):
            return result, {}
        provenance = result.meta.provenance
        return result.data, {
            "query_fingerprint": result.meta.fingerprint,
            "channel": result.meta.channel,
            "provenance": {
                "provider": provenance.provider,
                "channel": provenance.channel,
                "capability": provenance.capability,
                "kind": provenance.kind.value,
                "observed_at_ns": provenance.observed_at_ns,
                "provider_timestamp": provenance.provider_timestamp,
                "cache_tier": provenance.cache_tier,
                "requested_provider": provenance.requested_provider,
                "fallback": provenance.fallback,
            },
        }

    def execute(self, request: QueryRequest) -> QueryResponse:
        timeout = request.metadata.get("timeout")
        context = ExecutionContext(
            timeout=float(timeout) if timeout is not None else None,
            metadata=dict(request.metadata),
        )
        base_metadata: dict[str, Any] = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "operation": request.operation,
            "request_key": request.request_key,
        }

        try:
            handler = self._handlers.get(request.operation)
            if handler is not None:
                result = handler(request.params)
                return QueryResponse.ok(
                    result,
                    **base_metadata,
                    execution="compat-handler",
                )

            plan = self.planner.build(request)
            if not plan.graph.nodes:
                return QueryResponse.fail(
                    f"unsupported operation: {request.operation}",
                    **base_metadata,
                )

            result = plan.execute(context)
            data, canonical_metadata = self._unwrap_result(result)
            metadata = dict(base_metadata)
            metadata["execution"] = "execution-plan"
            metadata.update(self._execution_metadata(context))
            metadata.update(canonical_metadata)
            return QueryResponse.ok(data, **metadata)
        except Exception as exc:
            metadata = dict(base_metadata)
            metadata["error_type"] = type(exc).__name__
            metadata.update(self._execution_metadata(context))
            return QueryResponse.fail(
                str(exc),
                code=str(getattr(exc, "code", "") or ""),
                **metadata,
            )
