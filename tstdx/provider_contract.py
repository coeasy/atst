"""Provider isolation contract primitives.

This module defines the architectural boundary for data Providers.

Rules:
- A Provider represents one independent data source.
- A Provider must not call another Provider internally.
- Runtime execution binds one request to one Provider identity.
- Cross-provider fallback/comparison belongs to orchestration layers.

The contract is intentionally dependency-free so every Provider implementation
can use it without importing other Provider implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Immutable identity of an independent data source."""

    name: str
    channel: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("provider name must not be empty")
        if not self.channel.strip():
            raise ValueError("provider channel must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderCapabilityContract:
    """Declared capabilities owned by one Provider."""

    provider: str
    capabilities: FrozenSet[str]

    def supports(self, capability: str) -> bool:
        return capability.strip().lower() in self.capabilities


@dataclass(frozen=True, slots=True)
class ProviderExecutionContract:
    """Execution metadata passed through Runtime boundaries."""

    provider: str
    channel: str
    capability: str

    def validate(self) -> None:
        for value, name in (
            (self.provider, "provider"),
            (self.channel, "channel"),
            (self.capability, "capability"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")


__all__ = [
    "ProviderIdentity",
    "ProviderCapabilityContract",
    "ProviderExecutionContract",
]
