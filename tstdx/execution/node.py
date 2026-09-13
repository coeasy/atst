# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionNode:
    """Single executable unit in a v14 execution graph."""

    name: str
    handler: Callable[..., Any]
    dependencies: list[str] = field(default_factory=list)

    def execute(self, context: Any, inputs: dict[str, Any] | None = None) -> Any:
        inputs = inputs or {}
        return self.handler(context, **inputs)
