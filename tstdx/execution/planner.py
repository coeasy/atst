from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..provider.router import ProviderAttempts, ProviderRouter
from .graph import ExecutionGraph
from .node import ExecutionNode
from .plan import ExecutionPlan
from .semantic import SemanticExecutionAdapter


class ExecutionPlanner:
    """Compile runtime orchestration into an execution DAG.

    ``QueryPlanner`` owns deterministic single-Provider semantic planning;
    ``ExecutionPlanner`` owns orchestration/fallback and delegates canonical
    Provider requests to :class:`SemanticExecutionAdapter`.
    """

    def __init__(
        self,
        router: ProviderRouter | None = None,
        provider_order: Sequence[str] | None = None,
        semantic: SemanticExecutionAdapter | None = None,
    ) -> None:
        self.router = router
        self.provider_order = tuple(provider_order) if provider_order else None
        self.semantic = semantic or SemanticExecutionAdapter()

    @staticmethod
    def _candidate_order(
        *,
        explicit_provider: Any,
        requested_order: Any,
        provider_order: Sequence[str] | None,
        router: ProviderRouter,
    ) -> tuple[str, ...]:
        if explicit_provider:
            return (str(explicit_provider),)
        order = requested_order or provider_order or router.names()
        if isinstance(order, str):
            return (order,)
        return tuple(str(item) for item in order)

    def build(self, request: Any) -> ExecutionPlan:
        graph = ExecutionGraph()
        if self.router is None:
            return ExecutionPlan(operation=request.operation, graph=graph)
        router = self.router

        explicit_provider = request.metadata.get("provider")
        requested_order = request.metadata.get("providers")
        candidates = self._candidate_order(
            explicit_provider=explicit_provider,
            requested_order=requested_order,
            provider_order=self.provider_order,
            router=router,
        )
        if not candidates:
            return ExecutionPlan(operation=request.operation, graph=graph)

        # Provenance should reflect caller intent, not an internal default policy.
        caller_requested_provider = candidates[0] if explicit_provider or requested_order else None

        def execute_provider(context: Any, **_: Any) -> Any:
            attempts: ProviderAttempts = []
            context.metadata["provider_attempts"] = attempts

            if self.semantic.can_execute(request, candidates):
                name, result = self.semantic.execute(
                    request=request,
                    router=router,
                    providers=candidates,
                    attempts=attempts,
                    requested_provider=caller_requested_provider,
                )
            elif explicit_provider:
                name = candidates[0]
                result = router.query(name, request, attempts=attempts)
            else:
                name, result = router.query_first(request, candidates, attempts=attempts)

            context.provider = name
            return result

        graph.add_node(ExecutionNode(name="provider", handler=execute_provider))
        return ExecutionPlan(
            operation=request.operation,
            graph=graph,
            output_node="provider",
        )
