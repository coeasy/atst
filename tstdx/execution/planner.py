from __future__ import annotations

from .plan import ExecutionPlan


class Planner:
    """Converts runtime requests into executable plans.

    Initial version intentionally keeps planning explicit and predictable.
    Optimizer passes will be introduced after provider migration.
    """

    def build(self, request) -> ExecutionPlan:
        return ExecutionPlan(operation=request.operation)
