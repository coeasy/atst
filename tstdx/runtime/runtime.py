from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .context import ExecutionContext
from .request import QueryRequest
from .response import QueryResponse


class Runtime:
    """v14 runtime kernel entrypoint.

    Initial implementation intentionally keeps execution small. Future phases
    add planner, DAG executor and provider routing behind this boundary.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, operation: str, handler: Callable[..., Any]) -> None:
        self._handlers[operation] = handler

    def execute(self, request: QueryRequest) -> QueryResponse:
        context = ExecutionContext()
        handler = self._handlers.get(request.operation)
        if handler is None:
            return QueryResponse.fail(
                f"unsupported operation: {request.operation}",
                request_id=context.request_id,
            )

        try:
            result = handler(request.params)
            return QueryResponse.ok(result, request_id=context.request_id)
        except Exception as exc:
            return QueryResponse.fail(str(exc), request_id=context.request_id)
