# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical Provider-first semantic L1 cache.

Entries are keyed by full QueryFingerprint and preserve original provenance.
Capacity is enforced with deterministic LRU eviction; the cache never performs
a whole-map flush simply because one new key arrives.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
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
SEMANTIC_CACHE_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class SemanticCacheEntry(Generic[T]):
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
    ) -> "SemanticCacheEntry[T]":
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
        if plan.spec.currentness == "live" and self.provenance.kind is not ProvenanceKind.DIRECT:
            return False

        now = time.time_ns() if now_ns is None else int(now_ns)
        if self.expires_at_ns is not None and now >= self.expires_at_ns:
            return False
        if plan.spec.max_age is not None:
            max_age_ns = int(float(plan.spec.max_age) * 1_000_000_000)
            age_ns = max(0, now - self.provenance.observed_at_ns)
            if age_ns > max_age_ns:
                return False
        return True

    def to_result(self, plan: QueryPlan, *, cache_tier: str) -> QueryResult[T]:
        return QueryResult(
            data=copy.deepcopy(self.data),
            meta=ResultMeta.from_plan(plan, self.provenance.cached(cache_tier)),
        )


class SemanticResultCache:
    """Thread-safe bounded LRU semantic cache."""

    def __init__(self, *, tier: str = "l1", maxsize: int = 4096) -> None:
        normalized_tier = str(tier).strip().lower()
        if not normalized_tier:
            raise ValueError("tier must not be empty")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.tier = normalized_tier
        self.maxsize = int(maxsize)
        self._data: OrderedDict[str, SemanticCacheEntry[Any]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.rejects = 0

    def get(self, plan: QueryPlan, *, now_ns: int | None = None) -> QueryResult[Any] | None:
        key = plan.fingerprint.value
        try:
            with self._lock:
                entry = self._data.get(key)
                if not isinstance(entry, SemanticCacheEntry) or not entry.matches(plan, now_ns=now_ns):
                    if key in self._data:
                        self._data.pop(key, None)
                        self.rejects += 1
                    self.misses += 1
                    return None
                self._data.move_to_end(key, last=True)
                result = entry.to_result(plan, cache_tier=self.tier)
                self.hits += 1
                return result
        except Exception:
            with self._lock:
                if self._data.pop(key, None) is not None:
                    self.rejects += 1
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
        key = plan.fingerprint.value
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            while len(self._data) >= self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1
            self._data[key] = entry

    def invalidate(self, plan: QueryPlan) -> bool:
        with self._lock:
            return self._data.pop(plan.fingerprint.value, None) is not None

    def clear(self) -> int:
        with self._lock:
            size = len(self._data)
            self._data.clear()
            return size

    def metrics(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "rejects": self.rejects,
            }
