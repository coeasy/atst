# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-first semantic cache with provenance-preserving retrieval.

This cache intentionally sits beside the legacy ``KlineCache`` / ``QuoteCache``.
Legacy caches remain compatibility helpers; this module is the v11 runtime path.
A cache entry is valid only for the exact ``QueryFingerprint`` and the exact
Provider/Channel/Capability identity carried by its ``QueryPlan``.
"""

from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .query import QueryPlan
from .result import Provenance, ProvenanceKind, QueryResult, ResultMeta

__all__ = [
    "SEMANTIC_CACHE_SCHEMA_VERSION",
    "SemanticCacheEntry",
    "SemanticResultCache",
]

T = TypeVar("T")
SEMANTIC_CACHE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SemanticCacheEntry(Generic[T]):
    """Immutable semantic cache record."""

    schema_version: int
    fingerprint: str
    provider: str
    channel: str
    capability: str
    stored_at_ns: int
    expires_at_ns: int | None
    data: T
    provenance: Provenance

    @classmethod
    def from_result(
        cls,
        result: QueryResult[T],
        *,
        ttl: float | None,
        now_ns: int | None = None,
    ) -> SemanticCacheEntry[T]:
        now = time.time_ns() if now_ns is None else int(now_ns)
        if now <= 0:
            raise ValueError("now_ns must be positive")
        if ttl is not None and ttl < 0:
            raise ValueError("ttl must be >= 0 or None")
        expires = None if ttl is None else now + int(float(ttl) * 1_000_000_000)
        meta = result.meta
        return cls(
            schema_version=SEMANTIC_CACHE_SCHEMA_VERSION,
            fingerprint=meta.fingerprint,
            provider=meta.provider,
            channel=meta.channel,
            capability=meta.capability,
            stored_at_ns=now,
            expires_at_ns=expires,
            data=copy.deepcopy(result.data),
            provenance=Provenance(
                provider=meta.provenance.provider,
                channel=meta.provenance.channel,
                capability=meta.provenance.capability,
                kind=meta.provenance.kind,
                observed_at_ns=meta.provenance.observed_at_ns,
                provider_timestamp=meta.provenance.provider_timestamp,
                cache_tier=None,
                requested_provider=meta.provenance.requested_provider,
                fallback=meta.provenance.fallback,
            ),
        )

    def matches(self, plan: QueryPlan, *, now_ns: int | None = None) -> bool:
        if self.schema_version != SEMANTIC_CACHE_SCHEMA_VERSION:
            return False
        if self.fingerprint != plan.fingerprint.value:
            return False
        if (self.provider, self.channel, self.capability) != (
            plan.provider,
            plan.channel,
            plan.spec.capability,
        ):
            return False
        if (self.provenance.provider, self.provenance.channel, self.provenance.capability) != (
            self.provider,
            self.channel,
            self.capability,
        ):
            return False

        # A LIVE query must never be satisfied by replay/synthetic provenance.
        # Cache retrieval may add a cache tier, but it cannot upgrade origin trust.
        if plan.spec.currentness == "live" and self.provenance.kind is not ProvenanceKind.DIRECT:
            return False

        now = time.time_ns() if now_ns is None else int(now_ns)
        if self.expires_at_ns is not None and now >= self.expires_at_ns:
            return False

        # Query-level freshness is independent from cache TTL.  A cache item may
        # still be physically resident while being too old for a stricter caller.
        if plan.spec.max_age is not None:
            max_age_ns = int(float(plan.spec.max_age) * 1_000_000_000)
            age_ns = max(0, now - self.provenance.observed_at_ns)
            if age_ns > max_age_ns:
                return False
        return True

    def to_result(self, plan: QueryPlan, *, cache_tier: str) -> QueryResult[T]:
        provenance = self.provenance.cached(cache_tier)
        return QueryResult(
            data=copy.deepcopy(self.data),
            meta=ResultMeta.from_plan(plan, provenance),
        )


class SemanticResultCache:
    """Thread-safe semantic cache keyed by canonical query fingerprint."""

    def __init__(self, *, tier: str = "l1", maxsize: int = 4096) -> None:
        normalized_tier = str(tier).strip().lower()
        if not normalized_tier:
            raise ValueError("tier must not be empty")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.tier = normalized_tier
        self.maxsize = int(maxsize)
        self._data: dict[str, SemanticCacheEntry[Any]] = {}
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, plan: QueryPlan, *, now_ns: int | None = None) -> QueryResult[Any] | None:
        key = plan.fingerprint.value
        try:
            with self._lock:
                entry = self._data.get(key)
                if not isinstance(entry, SemanticCacheEntry) or not entry.matches(
                    plan, now_ns=now_ns
                ):
                    if key in self._data:
                        self._data.pop(key, None)
                    self.misses += 1
                    return None
                result = entry.to_result(plan, cache_tier=self.tier)
                self.hits += 1
                return result
        except Exception:
            with self._lock:
                self._data.pop(key, None)
                self.misses += 1
            return None

    def put(
        self,
        plan: QueryPlan,
        result: QueryResult[Any],
        *,
        ttl: float | None,
        now_ns: int | None = None,
    ) -> None:
        expected = (plan.provider, plan.channel, plan.spec.capability, plan.fingerprint.value)
        actual = (
            result.meta.provider,
            result.meta.channel,
            result.meta.capability,
            result.meta.fingerprint,
        )
        if actual != expected:
            raise ValueError("cache result identity does not match QueryPlan")
        entry = SemanticCacheEntry.from_result(result, ttl=ttl, now_ns=now_ns)
        with self._lock:
            if len(self._data) >= self.maxsize and plan.fingerprint.value not in self._data:
                self._data.clear()
                self.evictions += 1
            self._data[plan.fingerprint.value] = entry

    def invalidate(self, plan: QueryPlan) -> bool:
        with self._lock:
            return self._data.pop(plan.fingerprint.value, None) is not None

    def clear(self) -> int:
        with self._lock:
            size = len(self._data)
            self._data.clear()
            return size
