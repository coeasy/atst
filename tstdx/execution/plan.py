from __future__ import annotations

from dataclasses import dataclass, field

from .graph import ExecutionGraph


@dataclass
class ExecutionPlan:
    """Executable query plan generated from a request."""

    operation: str
    graph: ExecutionGraph = field(default_factory=ExecutionGraph)

    def execute(self, context):
        return self.graph.execute(context)
