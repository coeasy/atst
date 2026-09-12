from __future__ import annotations

from dataclasses import dataclass, field

from .node import ExecutionNode


@dataclass
class ExecutionGraph:
    nodes: dict[str, ExecutionNode] = field(default_factory=dict)

    def add_node(self, node: ExecutionNode) -> None:
        self.nodes[node.name] = node

    def resolve_order(self) -> list[ExecutionNode]:
        ordered: list[ExecutionNode] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            node = self.nodes[name]
            for dep in node.dependencies:
                visit(dep)
            ordered.append(node)

        for name in self.nodes:
            visit(name)
        return ordered

    def execute(self, context):
        results = {}
        for node in self.resolve_order():
            inputs = {d: results[d] for d in node.dependencies}
            results[node.name] = node.execute(context, inputs)
        return results
