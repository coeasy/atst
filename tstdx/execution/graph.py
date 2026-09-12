from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .node import ExecutionNode


@dataclass
class ExecutionGraph:
    """Directed acyclic graph of executable runtime nodes."""

    nodes: dict[str, ExecutionNode] = field(default_factory=dict)

    def add_node(self, node: ExecutionNode) -> None:
        if node.name in self.nodes:
            raise ValueError(f"duplicate execution node: {node.name}")
        self.nodes[node.name] = node

    def resolve_order(self) -> list[ExecutionNode]:
        ordered: list[ExecutionNode] = []
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(name: str) -> None:
            if name not in self.nodes:
                parent = stack[-1] if stack else "<root>"
                raise ValueError(f"execution node {parent!r} depends on missing node {name!r}")

            status = state.get(name, 0)
            if status == 2:
                return
            if status == 1:
                try:
                    start = stack.index(name)
                except ValueError:
                    start = 0
                cycle = " -> ".join([*stack[start:], name])
                raise ValueError(f"execution graph contains a cycle: {cycle}")

            state[name] = 1
            stack.append(name)
            node = self.nodes[name]
            for dependency in node.dependencies:
                visit(dependency)
            stack.pop()
            state[name] = 2
            ordered.append(node)

        for name in self.nodes:
            visit(name)
        return ordered

    def execute(self, context: Any) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for node in self.resolve_order():
            inputs = {dependency: results[dependency] for dependency in node.dependencies}
            results[node.name] = node.execute(context, inputs)
        return results
