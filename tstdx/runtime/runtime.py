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

        if planner is None:
            self.router = router or ProviderRouter()
            self.planner = Planner(self.router, provider_order=provider_order)
        else:
            if router is not None and planner.router is not None and planner.router is not router:
                raise ValueError("planner and runtime must use the same provider router")
            self.router = router or planner.router or ProviderRouter()
            if planner.router is None:
                planner.router = self.router
            if provider_order is not None:
                planner.provider_order = tuple(provider_order)
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
            "cache_key": request.cache_key,
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
            metadata = dict(base_metadata)
            metadata["execution"] = "execution-plan"
            metadata.update(self._execution_metadata(context))
            return QueryResponse.ok(result, **metadata)
        except Exception as exc:
            metadata = dict(base_metadata)
            metadata["error_type"] = type(exc).__name__
            metadata.update(self._execution_metadata(context))
            return QueryResponse.fail(
                str(exc),
                code=str(getattr(exc, "code", "") or ""),
                **metadata,
            )
