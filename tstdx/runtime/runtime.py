# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..execution.planner import ExecutionPlanner
from ..execution.semantic import SemanticExecutionAdapter
from ..provider.base import Provider
from ..provider.router import ProviderRouter
from ..result import QueryResult
from .context import ExecutionContext
from .request import QueryRequest
from .response import QueryResponse

if TYPE_CHECKING:
    from ..typed_query import CapabilityQuery
    from .legacy_bridge import LegacyRuntimeBridge
    from .stream import StreamHandle


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
        bridge: LegacyRuntimeBridge | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._subscriptions: dict[str, StreamHandle] = {}
        self._subscription_seq: int = 0
        self._bridge = bridge

        if planner is None:
            self.router = router or ProviderRouter()
            semantic = SemanticExecutionAdapter(bridge=bridge)
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
            if bridge is not None:
                planner.semantic._bridge = bridge
            self.planner = planner

    def register(self, operation: str, handler: Callable[..., Any]) -> None:
        if not operation:
            raise ValueError("operation must not be empty")
        self._handlers[operation] = handler

    def register_provider(self, provider: Provider) -> None:
        self.router.register(provider)

    def subscribe(
        self,
        capability: str,
        symbols: str | tuple[str, ...],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        subscription_id: str | None = None,
    ) -> StreamHandle:
        """Compile a StreamPlan, bind a fail-closed lifecycle, register it.

        The returned handle owns the compiled plan and lifecycle state machine;
        the actual data pull is driven by an external worker using ``plan``.
        ``capability`` is preserved for call-site clarity (currently only
        ``quotes`` is supported by StreamPlanner).
        """
        del capability  # reserved for future non-quotes capabilities
        from .stream import StreamHandle, runtime_subscribe

        handle = runtime_subscribe(
            self,
            symbols,
            provider=provider,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
        )
        sid = subscription_id or f"stream-{self._subscription_seq + 1}"
        if sid in self._subscriptions:
            raise ValueError(f"subscription id already in use: {sid}")
        self._subscription_seq += 1
        bound_handle = StreamHandle(
            plan=handle.plan,
            lifecycle=handle.lifecycle,
            id=sid,
        )
        self._subscriptions[sid] = bound_handle
        return bound_handle

    def unsubscribe(self, subscription_id: str) -> StreamHandle | None:
        """Idempotently stop a subscription. Returns the removed handle, else None."""
        handle = self._subscriptions.pop(subscription_id, None)
        if handle is None:
            return None
        handle.begin_stop()
        handle.close()
        return handle

    def get_subscription(self, subscription_id: str) -> StreamHandle | None:
        return self._subscriptions.get(subscription_id)

    def subscriptions(self) -> Mapping[str, StreamHandle]:
        """Return a snapshot of active subscriptions keyed by id."""
        return dict(self._subscriptions)

    def execute_typed(
        self,
        query: CapabilityQuery,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> QueryResponse:
        """Execute one canonical-ready typed query through the same Runtime path."""
        from .typed import request_from_typed

        return self.execute(request_from_typed(query, metadata=metadata))

    def execute_batch(
        self,
        requests: Sequence[QueryRequest],
        *,
        max_concurrent: int = 8,
    ) -> list[QueryResponse]:
        """Execute many independent requests, returning order-preserving results.

        Every request is dispatched to its bound Provider directly (zero cache).
        Remaining requests are dispatched concurrently (up to ``max_concurrent``
        workers). Results are returned in the original order.
        """
        if not requests:
            return []
        if len(requests) == 1:
            return [self.execute(requests[0])]

        to_execute = list(enumerate(requests))
        results: list[QueryResponse | None] = [None] * len(requests)
        if max_concurrent <= 1 or len(to_execute) <= 1:
            for i, req in to_execute:
                results[i] = self.execute(req)
        else:
            from concurrent.futures import ThreadPoolExecutor

            def _run(pair: tuple[int, QueryRequest]) -> tuple[int, QueryResponse]:
                return pair[0], self.execute(pair[1])

            with ThreadPoolExecutor(max_workers=min(max_concurrent, len(to_execute))) as ex:
                for idx, resp in ex.map(_run, to_execute):
                    results[idx] = resp

        return [r for r in results if r is not None]

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
