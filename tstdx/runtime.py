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
        self.planner = QueryPlanner(default_provider=default_provider)
        self.executor = DirectProviderExecutor(
            timeout=timeout,
            hosts=hosts,
            vipdoc_root=vipdoc_root,
        )
        self.cache = cache or SemanticResultCache(tier="l1")
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

    def __enter__(self) -> "UnifiedRuntime":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _promote_l2(self, plan: QueryPlan) -> QueryResult[Any] | None:
        if self.persistent_cache is None:
            return None
        hit = self.persistent_cache.get_with_ttl(plan)
        if hit is None:
            return None
        result, remaining_ttl = hit
        promotion_ttl = remaining_ttl
        if self.cache_ttl is not None:
            promotion_ttl = (
                self.cache_ttl
                if promotion_ttl is None
                else min(float(self.cache_ttl), float(promotion_ttl))
            )
        self.cache.put(plan, result, ttl=promotion_ttl)
        return result

    def execute(
        self,
        spec: QuerySpec,
        *,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        plan = self.planner.compile(spec)
        # These operations depend on mutable local state and/or perform writes.
        # Their full freshness/side-effect identity is not representable by the
        # current QueryFingerprint, so semantic/negative caches are forbidden.
        use_cache = use_cache and plan.spec.capability not in _UNCACHEABLE_CAPABILITIES

        if use_cache:
            hit = self.cache.get(plan)
            if hit is not None:
                return hit
            hit = self._promote_l2(plan)
            if hit is not None:
                return hit
            cached_error = self.negative_cache.get(plan)
            if cached_error is not None:
                raise cached_error

        def _leader() -> QueryResult[Any]:
            if use_cache:
                hit = self.cache.get(plan)
                if hit is not None:
                    return hit
                hit = self._promote_l2(plan)
                if hit is not None:
                    return hit
                cached_error = self.negative_cache.get(plan)
                if cached_error is not None:
                    raise cached_error
            try:
                result = self.executor.execute(plan)
            except Exception as exc:
                if use_cache:
                    self.negative_cache.put(plan, exc)
                raise
            if use_cache:
                self.cache.put(plan, result, ttl=self.cache_ttl)
                if self.persistent_cache is not None:
                    with contextlib.suppress(Exception):
                        self.persistent_cache.put(
                            plan,
                            result,
                            ttl=self.persistent_ttl,
                        )
                self.negative_cache.invalidate(plan)
            return result

        return self.singleflight.do(plan, _leader)

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "quotes",
                symbols=symbols,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> BatchResult[QueryResult[Any]]:
        """Execute independently auditable quote requests without hidden fallback."""
        items: dict[str, BatchItem[QueryResult[Any]]] = {}
        for raw_symbol in symbols:
            symbol = normalize_symbol(raw_symbol)
            if symbol in items:
                continue
            try:
                result = self.quotes(
                    symbol,
                    provider=provider,
                    currentness=currentness,
                    max_age=max_age,
                    use_cache=use_cache,
                )
            except Exception as exc:
                items[symbol] = BatchItem("failed", error=exc)
                continue
            items[symbol] = (
                BatchItem("missing")
                if not result.data
                else BatchItem("ok", value=result)
            )
        return BatchResult.build(items)

    def bars(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "bars",
                symbols=symbol,
                provider=provider,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "snapshot",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def minute(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "minute",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def trades(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        start: int = 0,
        count: int = 0,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "trades",
                symbols=symbol,
                provider=provider,
                start=start,
                count=count,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_count",
                provider=provider,
                market=market,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_list",
                provider=provider,
                market=market,
                start=start,
                currentness=currentness,
                max_age=max_age,
            ),
            use_cache=use_cache,
        )
