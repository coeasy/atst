# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Provider isolation runtime guards.

This module provides small validation primitives used during the Provider
Isolation migration.  Execution layers can call these guards before dispatching
an implementation to guarantee that the requested provider identity is not
silently replaced by another provider.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderIdentityMismatchError(RuntimeError):
    """Raised when execution identity differs from the requested provider."""


@dataclass(frozen=True, slots=True)
class ProviderExecutionIdentity:
    provider: str
    channel: str
    capability: str

    def normalized(self) -> ProviderExecutionIdentity:
        return ProviderExecutionIdentity(
            provider=self.provider.strip().lower(),
            channel=self.channel.strip().lower(),
            capability=self.capability.strip().lower(),
        )


def validate_execution_identity(
    requested: ProviderExecutionIdentity,
    executing: ProviderExecutionIdentity,
) -> None:
    """Ensure a plan executes on exactly the requested Provider identity.

    Provider fallback, if required, belongs to an explicit orchestration layer.
    The execution layer itself must never mutate Provider identity.
    """

    left = requested.normalized()
    right = executing.normalized()

    if left != right:
        raise ProviderIdentityMismatchError(
            f"Provider execution identity mismatch: requested={left!r}, executing={right!r}"
        )
