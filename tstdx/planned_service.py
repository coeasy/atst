# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Query-planned public market-data service.

``tstdx.service.UnifiedMarketDataService`` remains the Provider execution core.
This module layers deterministic QuerySpec/QueryPlan compilation, one total
execution deadline, batch de-duplication, SingleFlight and an opt-in semantic L1
cache on top without reimplementing any Provider adapter or fallback logic.

Cache policy is intentionally strict: ``max_age=None`` (the default) never reads
or writes query cache, so default high-level calls remain direct Provider
fetches. A positive ``max_age`` is an explicit caller freshness policy.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .domain.models import Bar, Quote
from .errors import ValidationError
from .execution import BatchPlanner, SingleFlight
from .freshness import FRESHNESS, validate_freshness
from .query import QueryPlan, QueryPlanner, QuerySpec
from .semantic_cache import SemanticQueryCache
from .service import FreshnessEvidence, QueryResult, ResultMeta
from .service import UnifiedMarketDataService as ProviderCoreService

__all__ = [
    "UnifiedMarketDataService",
    "ProviderCoreService",
    "market_data",
]


class UnifiedMarketDataService(ProviderCoreService):
    """Provider-bound service with canonical query planning and coordination."""

    def __init__(
        self,
        *args: Any,
        planner: QueryPlanner | None = None,
        singleflight: SingleFlight | None = None,
        query_cache: SemanticQueryCache | None = None,
        default_deadline_ms: int = 5000,
        **kwargs: Any,
    ) -> None:
        if default_deadline_ms <= 0:
            raise ValueError("default_deadline_ms must be > 0")
        super().__init__(*args, **kwargs)
        self.planner = planner or QueryPlanner()
        self.singleflight = singleflight or SingleFlight()
        self.query_cache = query_cache or SemanticQueryCache()
        self.default_deadline_ms = int(default_deadline_ms)

    def compile(self, spec: QuerySpec) -> QueryPlan:
        """Compile without I/O; useful for diagnostics, tests and integrations."""
        return self.planner.compile(spec)

    def query(
        self,
        spec: QuerySpec,
        *,
        with_meta: bool = True,
    ) -> Any:
        """Execute one canonical QuerySpec through the typed service methods."""
        plan = self.compile(spec)
        if plan.spec.capability == "quotes":
            return self._execute_quotes_plan(plan, with_meta=with_meta)
        if plan.spec.capability == "bars":
            return self._execute_bars_plan(plan, with_meta=with_meta)
        raise ValidationError(
            f"统一 query() 尚未接入 capability {plan.spec.capability!r}",
            context={
                "provider": plan.provider,
                "channel": plan.channel,
                "capability": plan.spec.capability,
            },
        )

    @staticmethod
    def _cache_enabled(plan: QueryPlan) -> bool:
        return plan.spec.max_age is not None and plan.spec.max_age > 0

    @staticmethod
    def _as_cache_hit(
        result: QueryResult[Any],
        *,
        max_age: float,
    ) -> QueryResult[Any]:
        """Re-validate cached provenance; never pretend a hit is direct I/O."""
        meta = result.meta
        original = meta.freshness
        freshness = FreshnessEvidence(
            origin="cache",
            observed_at_ns=original.observed_at_ns,
            provider_timestamp=original.provider_timestamp,
            cache_hit=True,
            replay=False,
            synthetic=False,
        )
        base_profile = FRESHNESS.get(meta.provider, meta.channel, meta.capability)
        cache_profile = replace(
            base_profile,
            require_direct=False,
            allow_cache=True,
            max_observation_age_seconds=max_age,
        )
        status = validate_freshness(
            freshness,
            provider=meta.provider,
            channel=meta.channel,
            capability=meta.capability,
            profile=cache_profile,
            now_ns=time.time_ns(),
            require_live=True,
        )
        status = replace(status, basis=f"bounded_cache:{status.basis}")
        cached_meta = ResultMeta(
            provider=meta.provider,
            channel=meta.channel,
            capability=meta.capability,
            observed_at_ns=meta.observed_at_ns,
            freshness=freshness,
            freshness_status=status,
            real=meta.real,
            fallback=False,
        )
        return QueryResult(data=result.data, meta=cached_meta)

    def _cache_get(self, plan: QueryPlan) -> QueryResult[Any] | None:
        if not self._cache_enabled(plan):
            return None
        max_age = float(plan.spec.max_age or 0.0)
        cached = self.query_cache.get(plan.fingerprint.value, max_age=max_age)
        if cached is None:
            return None
        if not isinstance(cached, QueryResult):
            self.query_cache.invalidate(plan.fingerprint.value)
            return None
        return self._as_cache_hit(cached, max_age=max_age)

    def _cache_put(self, plan: QueryPlan, result: QueryResult[Any]) -> None:
        if self._cache_enabled(plan):
            self.query_cache.put(plan.fingerprint.value, result)

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        source: str | None = None,
        with_meta: bool = False,
        allow_partial: bool = False,
        deadline_ms: int | None = None,
        max_age: float | None = None,
        allow_stale: bool = False,
    ) -> list[Quote] | QueryResult[list[Quote]]:
        seq = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        spec = QuerySpec.build(
            "quotes",
            symbols=seq,
            provider=provider,
            source=source,
            allow_partial=allow_partial,
            deadline_ms=deadline_ms or self.default_deadline_ms,
            max_age=max_age,
            allow_stale=allow_stale,
        )
        return self.query(spec, with_meta=with_meta)

    def _execute_quotes_plan(
        self,
        plan: QueryPlan,
        *,
        with_meta: bool,
    ) -> list[Quote] | QueryResult[list[Quote]]:
        cached = self._cache_get(plan)
        if cached is not None:
            plan.budget.ensure_remaining("cache_read")
            return cached if with_meta else cached.data

        batch = BatchPlanner.symbols(list(plan.spec.symbols))

        def fetch() -> QueryResult[list[Quote]]:
            plan.budget.begin_attempt()
            result = super(UnifiedMarketDataService, self).quotes(
                list(batch.unique),
                provider=plan.provider,
                with_meta=True,
            )
            if not isinstance(result, QueryResult):
                raise RuntimeError("ProviderCoreService.quotes(with_meta=True) contract violated")
            plan.budget.ensure_remaining("provider_response")
            data = batch.fanout(
                list(result.data),
                allow_partial=plan.spec.allow_partial,
            )
            coordinated = QueryResult(data=data, meta=result.meta)
            self._cache_put(plan, coordinated)
            return coordinated

        result = self.singleflight.do(
            plan.fingerprint.value,
            fetch,
            timeout=plan.budget.remaining_s(),
        )
        plan.budget.ensure_remaining("serialize")
        return result if with_meta else result.data

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str = "",
        provider: str | None = None,
        source: str | None = None,
        with_meta: bool = False,
        deadline_ms: int | None = None,
        max_age: float | None = None,
        allow_stale: bool = False,
    ) -> list[Bar] | QueryResult[list[Bar]]:
        spec = QuerySpec.build(
            "bars",
            symbols=(symbol,),
            provider=provider,
            source=source,
            period=period,
            count=count,
            start=start,
            adjustment=adjust,
            deadline_ms=deadline_ms or self.default_deadline_ms,
            max_age=max_age,
            allow_stale=allow_stale,
        )
        return self.query(spec, with_meta=with_meta)

    def _execute_bars_plan(
        self,
        plan: QueryPlan,
        *,
        with_meta: bool,
    ) -> list[Bar] | QueryResult[list[Bar]]:
        cached = self._cache_get(plan)
        if cached is not None:
            plan.budget.ensure_remaining("cache_read")
            return cached if with_meta else cached.data

        symbol = plan.spec.symbols[0]

        def fetch() -> QueryResult[list[Bar]]:
            plan.budget.begin_attempt()
            result = super(UnifiedMarketDataService, self).bars(
                symbol,
                period=plan.spec.period or "day",
                count=plan.spec.count,
                start=plan.spec.start,
                adjust=plan.spec.adjustment,
                provider=plan.provider,
                with_meta=True,
            )
            if not isinstance(result, QueryResult):
                raise RuntimeError("ProviderCoreService.bars(with_meta=True) contract violated")
            plan.budget.ensure_remaining("provider_response")
            self._cache_put(plan, result)
            return result

        result = self.singleflight.do(
            plan.fingerprint.value,
            fetch,
            timeout=plan.budget.remaining_s(),
        )
        plan.budget.ensure_remaining("serialize")
        return result if with_meta else result.data


def market_data(**kwargs: Any) -> UnifiedMarketDataService:
    return UnifiedMarketDataService(**kwargs)
