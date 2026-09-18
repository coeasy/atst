# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical provider-first execution runtime for v13."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

from .batch import BatchItem, BatchResult, NegativeCache, SingleFlight
from .cache_persistent import PersistentSemanticCache
from .cache_semantic import SemanticResultCache
from .direct_provider import DirectProviderExecutor
from .domain.symbol import normalize_symbol
from .query import QueryPlan, QueryPlanner, QuerySpec
from .result import QueryResult
from .runtime_audit import audit_runtime
from .runtime_cache_adapter import RuntimeCacheAdapter
from .runtime_cache_policy import should_negative_cache
from .runtime_identity import (
    RuntimeCacheIdentity,
    cache_identity_from_plan,
    execution_identity_from_plan,
)
from .runtime_provenance import validate_runtime_provenance

__all__ = ["UnifiedRuntime"]

_UNCACHEABLE_CAPABILITIES = frozenset({"adjusted_bars", "sync_daily"})


class UnifiedRuntime:
    """Compile, cache, coalesce and execute one exact Provider plan."""

    def __init__(
        self,
        *,
        default_provider: str = "tdx",
        timeout: float = 5.0,
        hosts: list[str] | None = None,
        vipdoc_root: str | None = None,
        cache: SemanticResultCache | None = None,
        cache_ttl: float | None = 5.0,
        persistent_cache: PersistentSemanticCache | None = None,
        persistent_path: str | None = None,
        persistent_ttl: float | None = 300.0,
        negative_cache: NegativeCache | None = None,
        singleflight: SingleFlight | None = None,
    ) -> None:
        if persistent_cache is not None and persistent_path is not None:
            raise ValueError(
                "persistent_cache and persistent_path are mutually exclusive"
            )
        audit_runtime()
        self.planner = QueryPlanner(default_provider=default_provider)
        self.executor = DirectProviderExecutor(
            timeout=timeout,
            hosts=hosts,
            vipdoc_root=vipdoc_root,
        )
        self.cache = cache or SemanticResultCache(tier="l1")
        self.cache_adapter = RuntimeCacheAdapter(self.cache)
        self.cache_ttl = cache_ttl
        self._owns_persistent_cache = (
            persistent_cache is None and persistent_path is not None
        )
        self.persistent_cache = (
            persistent_cache
            if persistent_cache is not None
            else (
                PersistentSemanticCache(persistent_path)
                if persistent_path is not None
                else None
            )
        )
        self.persistent_ttl = persistent_ttl
        self.negative_cache = negative_cache or NegativeCache(ttl=1.0)
        self.singleflight = singleflight or SingleFlight()

    def close(self) -> None:
        if self._owns_persistent_cache and self.persistent_cache is not None:
            self.persistent_cache.close()
            self.persistent_cache = None
