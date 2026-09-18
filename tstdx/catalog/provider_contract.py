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


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Immutable identity of an independent data source."""

    provider: str
    channel: str

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("provider must not be empty")
        if not self.channel.strip():
            raise ValueError("provider channel must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderCapabilityContract:
    """Declared capabilities owned by one Provider."""

    provider: str
    capabilities: frozenset[str]

    def supports(self, capability: str) -> bool:
        return capability.strip().lower() in self.capabilities


@dataclass(frozen=True, slots=True)
class ProviderExecutionContract:
    """Execution metadata passed through Runtime boundaries.

    Binds one request to exactly one Provider identity. The identity is carried
    as a :class:`ProviderIdentity` so the execution layer never takes a flat,
    mutable provider string that could silently drift to another source.
    """

    identity: ProviderIdentity
    capability: str

    def assert_provider(self, provider: str) -> None:
        """Raise :class:`ValueError` if the bound identity is not ``provider``."""

        if self.identity.provider != provider:
            raise ValueError(f"execution targets {self.identity.provider!r}, not {provider!r}")

    def assert_identity(self, other: ProviderIdentity) -> None:
        """Raise :class:`ValueError` if the bound identity differs from ``other``."""

        if self.identity != other:
            raise ValueError(f"execution identity {self.identity!r} != requested {other!r}")


__all__ = [
    "ProviderIdentity",
    "ProviderCapabilityContract",
    "ProviderExecutionContract",
]
