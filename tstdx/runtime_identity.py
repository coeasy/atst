# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Runtime identity primitives for Provider isolation.

Runtime cache, single-flight and negative-cache implementations should use these
identities instead of capability-only keys. Provider identity is part of the
semantic meaning of a query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeCacheIdentity:
    provider: str
    channel: str
    capability: str
    fingerprint: str

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.provider.strip().lower(),
            self.channel.strip().lower(),
            self.capability.strip().lower(),
            self.fingerprint,
        )


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


def _fingerprint_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def cache_identity_from_plan(plan: object) -> RuntimeCacheIdentity:
    """Build a provider-aware cache identity from a canonical QueryPlan."""

    spec = getattr(plan, "spec")
    return RuntimeCacheIdentity(
        provider=str(getattr(plan, "provider")),
        channel=str(getattr(plan, "channel")),
        capability=str(getattr(spec, "capability")),
        fingerprint=_fingerprint_value(getattr(plan, "fingerprint", "")),
    )


def execution_identity_from_plan(plan: object) -> RuntimeExecutionIdentity:
    spec = getattr(plan, "spec")
    return RuntimeExecutionIdentity(
        provider=str(getattr(plan, "provider")),
        channel=str(getattr(plan, "channel")),
        capability=str(getattr(spec, "capability")),
    )
