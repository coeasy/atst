# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Query-planned public market-data service.

``tstdx.service.UnifiedMarketDataService`` remains the Provider execution core.
This module layers deterministic QuerySpec/QueryPlan compilation, one total
execution deadline, batch de-duplication, SingleFlight and an opt-in semantic L1
cache on top without reimplementing any Provider adapter or fallback logic.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .batch import BatchResult
from .domain.models import Bar, Quote
from .domain.symbol import normalize_symbol
from .error_envelope import to_error_envelope
from .errors import IntegrityViolation, SourceUnavailable, TdxError, ValidationError
from .execution import BatchPlan, BatchPlanner, SingleFlight
from .failure import DEFAULT_FAILURE_POLICY, FailurePolicy
from .freshness import FRESHNESS, validate_freshness
from .query import QueryPlan, QueryPlanner, QuerySpec
from .semantic_cache import SemanticQueryCache
from .service import FreshnessEvidence, QueryResult, ResultMeta
from .service import UnifiedMarketDataService as ProviderCoreService

__all__ = [
    "UnifiedMarketDataService",
    "ProviderCoreService",
    "BatchResult",
    "market_data",
]


class UnifiedMarketDataService(ProviderCoreService):
    """Provider-bound service with canonical query planning and coordination."""

    def __init__(
        self,
        *args: Any,
        planner: QueryPlanner | None = None,
        default_provider: str | None = None,
        singleflight: SingleFlight | None = None,
        query_cache: SemanticQueryCache | None = None,
        failure_policy: FailurePolicy | None = None,
        default_deadline_ms: int = 5000,
        **kwargs: Any,
    ) -> None:
        if default_deadline_ms <= 0:
            raise ValueError("default_deadline_ms must be > 0")
        if planner is not None and default_provider is not None:
            raise ValueError("planner 与 default_provider 不能同时提供：默认 Provider 必须只有一个真相源")
        super().__init__(*args, **kwargs)
        if planner is not None:
            self.planner = planner
        else:
            if default_provider is None:
                from .config import get_config

                configured_provider = get_config().sources.default_provider
            else:
                configured_provider = default_provider
            self.planner = QueryPlanner(default_provider=configured_provider)
        self.singleflight = singleflight if singleflight is not None else SingleFlight()
        self.query_cache = query_cache if query_cache is not None else SemanticQueryCache()
        self.failure_policy = failure_policy if failure_policy is not None else DEFAULT_FAILURE_POLICY
        self.default_deadline_ms = int(default_deadline_ms)

    def compile(self, spec: QuerySpec) -> QueryPlan:
        return self.planner.compile(spec)

    def query(self, spec: QuerySpec, *, with_meta: bool = True) -> Any:
        plan = self.compile(spec)
        try:
            if plan.spec.capability == "quotes":
                if plan.spec.allow_partial:
                    return self._execute_quote_batch_plan(plan)
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
        except TdxError as exc:
            disposition = self.failure_policy.decide(exc, budget=plan.budget)
            exc.context.setdefault("provider", plan.provider)
            exc.context.setdefault("channel", plan.channel)
            exc.context.setdefault("capability", plan.spec.capability)
            exc.context.setdefault("query_id", plan.fingerprint.value)
            exc.context.setdefault("phase", "execution")
            exc.context.setdefault("fallback", False)
            for key, value in disposition.to_context().items():
                exc.context.setdefault(key, value)
            raise

    @staticmethod
    def _cache_enabled(plan: QueryPlan) -> bool:
        return (
            not plan.spec.allow_partial
            and plan.spec.max_age is not None
            and plan.spec.max_age > 0
        )

    @staticmethod
    def _as_cache_hit(result: QueryResult[Any], *, max_age: float) -> QueryResult[Any]:
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
        return QueryResult(
            data=result.data,
            meta=ResultMeta(
                provider=meta.provider,
                channel=meta.channel,
                capability=meta.capability,
                observed_at_ns=meta.observed_at_ns,
                freshness=freshness,
                freshness_status=status,
                real=meta.real,
                fallback=False,
            ),
        )

    def _cache_get(self, plan: QueryPlan) -> QueryResult[Any] | None:
        if not self._cache_enabled(plan):
            return None
        max_age = float(plan.spec.max_age if plan.spec.max_age is not None else 0.0)
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

    @staticmethod
    def _quote_map(batch: BatchPlan, rows: Sequence[Quote]) -> dict[str, Quote]:
        expected = set(batch.unique)
        by_symbol: dict[str, Quote] = {}
        for quote in rows:
            try:
                symbol = normalize_symbol(str(quote.code))
            except Exception as exc:
                raise IntegrityViolation(
                    "Provider quote 响应包含无法归一化的 code",
                    context={"code": str(quote.code)},
                    cause=exc,
                ) from exc
            if symbol not in expected:
                raise IntegrityViolation(
                    "Provider quote 响应包含未请求标的",
                    context={"symbol": symbol, "requested": list(batch.unique)},
                )
            if symbol in by_symbol:
                raise IntegrityViolation(
                    "Provider quote 响应包含重复标的",
                    context={"symbol": symbol},
                )
            by_symbol[symbol] = quote
        return by_symbol

    @classmethod
    def _align_quote_batch(cls, batch: BatchPlan, rows: Sequence[Quote]) -> list[Quote]:
        by_symbol = cls._quote_map(batch, rows)
        missing = [symbol for symbol in batch.unique if symbol not in by_symbol]
        if missing:
            raise SourceUnavailable(
                "Provider 批量行情缺少请求标的",
                context={
                    "missing_symbols": missing,
                    "requested_unique": len(batch.unique),
                    "received_unique": len(by_symbol),
                    "partial": bool(by_symbol),
                },
            )
        return [by_symbol[symbol] for symbol in batch.original]

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
    ) -> list[Quote] | QueryResult[list[Quote]] | BatchResult[Quote]:
        seq = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        spec = QuerySpec.build(
            "quotes",
            symbols=seq,
            provider=provider,
            source=source,
            allow_partial=allow_partial,
            deadline_ms=self.default_deadline_ms if deadline_ms is None else deadline_ms,
            max_age=max_age,
            allow_stale=allow_stale,
        )
        return self.query(spec, with_meta=with_meta)

    def quotes_batch(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        source: str | None = None,
        deadline_ms: int | None = None,
    ) -> BatchResult[Quote]:
        result = self.quotes(
            symbols,
            provider=provider,
            source=source,
            allow_partial=True,
            deadline_ms=deadline_ms,
        )
        if not isinstance(result, BatchResult):
            raise RuntimeError("quotes_batch contract violated")
        return result

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
                list(batch.unique), provider=plan.provider, with_meta=True
            )
            if not isinstance(result, QueryResult):
                raise RuntimeError("ProviderCoreService.quotes(with_meta=True) contract violated")
            plan.budget.ensure_remaining("provider_response")
            coordinated = QueryResult(
                data=self._align_quote_batch(batch, list(result.data)),
                meta=result.meta,
            )
            self._cache_put(plan, coordinated)
            return coordinated

        result = self.singleflight.do(
            plan.fingerprint.value,
            fetch,
            timeout=plan.budget.remaining_s(),
        )
        plan.budget.ensure_remaining("serialize")
        return result if with_meta else result.data

    def _execute_quote_batch_plan(self, plan: QueryPlan) -> BatchResult[Quote]:
        """Return auditable successes/errors; partial batches are never cached."""
        batch = BatchPlanner.symbols(list(plan.spec.symbols))

        def fetch() -> BatchResult[Quote]:
            plan.budget.begin_attempt()
            result = super(UnifiedMarketDataService, self).quotes(
                list(batch.unique), provider=plan.provider, with_meta=True
            )
            if not isinstance(result, QueryResult):
                raise RuntimeError("ProviderCoreService.quotes(with_meta=True) contract violated")
            plan.budget.ensure_remaining("provider_response")
            by_symbol = self._quote_map(batch, list(result.data))
            missing = [symbol for symbol in batch.unique if symbol not in by_symbol]
            errors = {}
            for symbol in missing:
                exc = SourceUnavailable(
                    "Provider 批量行情缺少请求标的",
                    context={
                        "provider": plan.provider,
                        "channel": plan.channel,
                        "capability": "quotes",
                        "query_id": plan.fingerprint.value,
                        "phase": "normalize",
                        "symbol": symbol,
                        "partial": True,
                        "fallback": False,
                        "retry_same_provider": False,
                        "terminal": True,
                        "provider_switch_allowed": False,
                    },
                )
                errors[symbol] = to_error_envelope(
                    exc,
                    provider=plan.provider,
                    channel=plan.channel,
                    capability="quotes",
                    query_id=plan.fingerprint.value,
                )
            items = tuple(by_symbol[symbol] for symbol in batch.original if symbol in by_symbol)
            return BatchResult(
                items=items,
                errors=errors,
                requested=batch.original,
                partial=bool(errors),
                meta=result.meta,
            )

        result = self.singleflight.do(
            plan.fingerprint.value,
            fetch,
            timeout=plan.budget.remaining_s(),
        )
        plan.budget.ensure_remaining("serialize")
        return result

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
            deadline_ms=self.default_deadline_ms if deadline_ms is None else deadline_ms,
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
