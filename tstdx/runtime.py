# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical provider-first execution runtime."""

from __future__ import annotations

from typing import Any

from .batch import NegativeCache, SingleFlight
from .cache_semantic import SemanticResultCache
from .direct_provider import DirectProviderExecutor
from .query import QueryPlanner, QuerySpec
from .result import QueryResult

__all__ = ["UnifiedRuntime"]


class UnifiedRuntime:
    """Compile, cache, single-flight and execute one exact Provider plan."""

    def __init__(
        self,
        *,
        default_provider: str = "tdx",
        timeout: float = 5.0,
        hosts: list[str] | None = None,
        vipdoc_root: str | None = None,
        cache: SemanticResultCache | None = None,
        cache_ttl: float | None = 5.0,
        negative_cache: NegativeCache | None = None,
        singleflight: SingleFlight | None = None,
    ) -> None:
        self.planner = QueryPlanner(default_provider=default_provider)
        self.executor = DirectProviderExecutor(
            timeout=timeout,
            hosts=hosts,
            vipdoc_root=vipdoc_root,
        )
        self.cache = cache or SemanticResultCache(tier="l1")
        self.cache_ttl = cache_ttl
        self.negative_cache = negative_cache or NegativeCache(ttl=1.0)
        self.singleflight = singleflight or SingleFlight()

    def execute(self, spec: QuerySpec, *, use_cache: bool = True) -> QueryResult[Any]:
        plan = self.planner.compile(spec)
        if use_cache:
            hit = self.cache.get(plan)
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
            try:
                result = self.executor.execute(plan)
            except Exception as exc:
                if use_cache:
                    self.negative_cache.put(plan, exc)
                raise
            if use_cache:
                self.cache.put(plan, result, ttl=self.cache_ttl)
                self.negative_cache.invalidate(plan)
            return result

        return self.singleflight.do(plan, _leader)

    def quotes(
        self,
        symbols: str | list[str] | tuple[str, ...],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        allow_partial: bool = False,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        spec = QuerySpec.build(
            "quotes",
            symbols=symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
            allow_partial=allow_partial,
        )
        return self.execute(spec, use_cache=use_cache)

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
        spec = QuerySpec.build(
            "bars",
            symbols=symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=currentness,
            max_age=max_age,
        )
        return self.execute(spec, use_cache=use_cache)
