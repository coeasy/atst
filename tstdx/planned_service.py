# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Query-planned public market-data service.

``tstdx.service.UnifiedMarketDataService`` remains the Provider execution core.
This module layers deterministic QuerySpec/QueryPlan compilation, one total
execution deadline, batch de-duplication and SingleFlight on top without
reimplementing any Provider adapter or fallback logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .domain.models import Bar, Quote
from .errors import ValidationError
from .execution import BatchPlanner, SingleFlight
from .query import QueryPlan, QueryPlanner, QuerySpec
from .service import QueryResult
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
        default_deadline_ms: int = 5000,
        **kwargs: Any,
    ) -> None:
        if default_deadline_ms <= 0:
            raise ValueError("default_deadline_ms must be > 0")
        super().__init__(*args, **kwargs)
        self.planner = planner or QueryPlanner()
        self.singleflight = singleflight or SingleFlight()
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
        batch = BatchPlanner.symbols(list(plan.spec.symbols))

        def fetch() -> QueryResult[list[Quote]]:
            plan.budget.begin_attempt()
            result = super(UnifiedMarketDataService, self).quotes(
                list(batch.unique),
                provider=plan.provider,
                with_meta=True,
            )
            if not isinstance(result, QueryResult):  # defensive contract guard
                raise RuntimeError("ProviderCoreService.quotes(with_meta=True) contract violated")
            plan.budget.ensure_remaining("provider_response")
            data = batch.fanout(
                list(result.data),
                allow_partial=plan.spec.allow_partial,
            )
            return QueryResult(data=data, meta=result.meta)

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
