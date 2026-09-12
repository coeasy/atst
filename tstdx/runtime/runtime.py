from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..execution.planner import Planner
from ..provider.base import Provider
from ..provider.router import ProviderRouter
from .context import ExecutionContext
from .request import QueryRequest
from .response import QueryResponse


class Runtime:
    """v14 runtime kernel entrypoint.

    Registered compatibility handlers are preserved, while unhandled operations
    are planned through the provider-backed execution graph.
    """

    def __init__(
        self,
        *,
        router: ProviderRouter | None = None,
        planner: Planner | None = None,
        provider_order: Sequence[str] | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}
        self.router = router or ProviderRouter()
        self.planner = planner or Planner(self.router, provider_order=provider_order)

    def register(self, operation: str, handler: Callable[..., Any]) -> None:
        if not operation:
            raise ValueError("operation must not be empty")
        self._handlers[operation] = handler

    def register_provider(self, provider: Provider) -> None:
        self.router.register(provider)

    def execute(self, request: QueryRequest) -> QueryResponse:
        timeout = request.metadata.get("timeout")
        context = ExecutionContext(
            timeout=float(timeout) if timeout is not None else None,
            metadata=dict(request.metadata),
        )

        try:
            handler = self._handlers.get(request.operation)
            if handler is not None:
                result = handler(request.params)
                return QueryResponse.ok(
                    result,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    execution="compat-handler",
                )

            plan = self.planner.build(request)
            if not plan.graph.nodes:
                return QueryResponse.fail(
                    f"unsupported operation: {request.operation}",
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                )

            result = plan.execute(context)
            metadata: dict[str, Any] = {
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "execution": "execution-plan",
            }
            if context.provider is not None:
                metadata["provider"] = context.provider
            return QueryResponse.ok(result, **metadata)
        except Exception as exc:
            metadata = {
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "error_type": type(exc).__name__,
            }
            if context.provider is not None:
                metadata["provider"] = context.provider
            return QueryResponse.fail(str(exc), **metadata)
