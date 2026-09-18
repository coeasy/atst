# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Execution identity for the single-kernel provenance guard.

Provider identity is part of the semantic meaning of a query: the kernel derives
a :class:`RuntimeExecutionIdentity` from the compiled plan and rejects any result
whose provenance points at a different provider or channel.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..query import QueryPlan


@dataclass(frozen=True, slots=True)
class RuntimeExecutionIdentity:
    provider: str
    channel: str
    capability: str

    def key(self) -> tuple[str, str, str]:
        return (
            self.provider.strip().lower(),
            self.channel.strip().lower(),
            self.capability.strip().lower(),
        )


def execution_identity_from_plan(plan: QueryPlan) -> RuntimeExecutionIdentity:
    return RuntimeExecutionIdentity(
        provider=str(plan.provider),
        channel=str(plan.channel),
        capability=str(plan.spec.capability),
    )
