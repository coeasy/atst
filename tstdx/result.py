# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical query result and provenance contracts.

Data origin and retrieval location are separate facts. The v17 runtime performs
**no** result caching, so every production provenance is built by
:meth:`Provenance.direct` and carries ``cache_tier=None``; the field is kept on
the wire precisely because callers assert it stays ``None``. Replay or
synthetic data can therefore never be mistaken for a live Provider read, and a
future tier stamp would have to be an explicit, visible decision rather than an
implicit side effect of the request path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar

from .errors import ValidationError
from .query import QueryPlan

__all__ = [
    "ProvenanceKind",
    "Provenance",
    "ResultMeta",
    "QueryResult",
]

T = TypeVar("T")


class ProvenanceKind(str, Enum):
    DIRECT = "direct"
    REPLAY = "replay"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True, slots=True)
class Provenance:
    provider: str
    channel: str
    capability: str
    kind: ProvenanceKind
    observed_at_ns: int
    provider_timestamp: str | None = None
    cache_tier: str | None = None
    requested_provider: str | None = None
    fallback: bool = False

    def __post_init__(self) -> None:
        if not self.provider or not self.channel or not self.capability:
            raise ValueError("provider/channel/capability must not be empty")
        if self.observed_at_ns <= 0:
            raise ValueError("observed_at_ns must be positive")
        if self.cache_tier is not None and not str(self.cache_tier).strip():
            raise ValueError("cache_tier must not be empty")
        if self.fallback and not self.requested_provider:
            raise ValueError("fallback provenance requires requested_provider")
        if self.requested_provider is not None and not str(self.requested_provider).strip():
            raise ValueError("requested_provider must not be empty")

    @classmethod
    def direct(
        cls,
        plan: QueryPlan,
        *,
        provider_timestamp: str | None = None,
        observed_at_ns: int | None = None,
    ) -> Provenance:
        return cls(
            provider=plan.provider,
            channel=plan.channel,
            capability=plan.spec.capability,
            kind=ProvenanceKind.DIRECT,
            observed_at_ns=time.time_ns() if observed_at_ns is None else int(observed_at_ns),
            provider_timestamp=provider_timestamp,
            requested_provider=plan.provider,
            fallback=False,
        )

    @property
    def real(self) -> bool:
        return self.kind is ProvenanceKind.DIRECT

    @property
    def replay(self) -> bool:
        return self.kind is ProvenanceKind.REPLAY

    @property
    def synthetic(self) -> bool:
        return self.kind is ProvenanceKind.SYNTHETIC


@dataclass(frozen=True, slots=True)
class ResultMeta:
    provider: str
    channel: str
    capability: str
    fingerprint: str
    provenance: Provenance

    @classmethod
    def from_plan(cls, plan: QueryPlan, provenance: Provenance) -> ResultMeta:
        expected = (plan.provider, plan.channel, plan.spec.capability)
        actual = (provenance.provider, provenance.channel, provenance.capability)
        if actual != expected:
            raise ValidationError(
                "QueryResult provenance 与 QueryPlan identity 不一致",
                context={
                    "plan_provider": plan.provider,
                    "plan_channel": plan.channel,
                    "plan_capability": plan.spec.capability,
                    "provenance_provider": provenance.provider,
                    "provenance_channel": provenance.channel,
                    "provenance_capability": provenance.capability,
                },
            )
        return cls(
            provider=plan.provider,
            channel=plan.channel,
            capability=plan.spec.capability,
            fingerprint=plan.fingerprint.value,
            provenance=provenance,
        )

    @property
    def source(self) -> str:
        """Compatibility alias; Provider remains the canonical identity."""
        return self.provider


@dataclass(frozen=True, slots=True)
class QueryResult(Generic[T]):
    data: T
    meta: ResultMeta

    @classmethod
    def from_plan(
        cls,
        data: T,
        *,
        plan: QueryPlan,
        provenance: Provenance,
    ) -> QueryResult[T]:
        return cls(data=data, meta=ResultMeta.from_plan(plan, provenance))
