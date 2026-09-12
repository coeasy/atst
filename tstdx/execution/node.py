from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ExecutionNode:
    """Single executable unit in a v14 execution graph."""

    name: str
    handler: Callable[..., Any]
    dependencies: list[str] = field(default_factory=list)

    def execute(self, context: Any, inputs: dict[str, Any] | None = None) -> Any:
        inputs = inputs or {}
        return self.handler(context, **inputs)
