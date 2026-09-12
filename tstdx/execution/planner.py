from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..provider.router import ProviderAttempts, ProviderRouter
from .graph import ExecutionGraph
from .node import ExecutionNode
from .plan import ExecutionPlan


class Planner:
    """Convert runtime requests into provider-backed execution plans."""

    def __init__(
        self,
        router: ProviderRouter | None = None,
        provider_order: Sequence[str] | None = None,
    ) -> None:
        self.router = router
        self.provider_order = tuple(provider_order) if provider_order else None

    def build(self, request: Any) -> ExecutionPlan:
        graph = ExecutionGraph()
        if self.router is None or not self.router.names():
            return ExecutionPlan(operation=request.operation, graph=graph)

        explicit_provider = request.metadata.get("provider")
        requested_order = request.metadata.get("providers")

        def execute_provider(context: Any, **_: Any) -> Any:
            attempts: ProviderAttempts = []
            context.metadata["provider_attempts"] = attempts
            if explicit_provider:
                name = str(explicit_provider)
                result = self.router.query(name, request, attempts=attempts)
            else:
                order = requested_order or self.provider_order
                if isinstance(order, str):
                    order = (order,)
                name, result = self.router.query_first(request, order, attempts=attempts)
            context.provider = name
            return result

        graph.add_node(ExecutionNode(name="provider", handler=execute_provider))
        return ExecutionPlan(
            operation=request.operation,
            graph=graph,
            output_node="provider",
        )
