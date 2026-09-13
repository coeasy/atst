# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph import ExecutionGraph


@dataclass
class ExecutionPlan:
    """Executable query plan generated from a runtime request."""

    operation: str
    graph: ExecutionGraph = field(default_factory=ExecutionGraph)
    output_node: str | None = None

    def execute(self, context: Any) -> Any:
        results = self.graph.execute(context)
        if self.output_node is None:
            return results
        if self.output_node not in results:
            raise RuntimeError(f"execution plan output node was not produced: {self.output_node}")
        return results[self.output_node]
