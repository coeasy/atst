# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Query-planned public market-data service.

``tstdx.service.UnifiedMarketDataService`` remains the Provider execution core.
This module layers deterministic QuerySpec/QueryPlan compilation, one total
execution deadline, batch de-duplication/chunking, SingleFlight, dynamic health
and semantic cache V2 without reimplementing Provider adapters or fallback.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .batch import BatchResult
from .cache_v2 import SQLiteSemanticQueryCache, TieredSemanticQueryCache
from .domain.models import Bar, Quote
from .domain.symbol import normalize_symbol
from .error_envelope import ErrorEnvelope, to_error_envelope
from .errors import (
    InternalError,
    IntegrityViolation,
    SourceUnavailable,
    TdxError,
    ValidationError,
)
from .execution import BatchPlan, BatchPlanner, SingleFlight
from .failure import DEFAULT_FAILURE_POLICY, FailurePolicy
from .freshness import (
    FRESHNESS,
    FreshnessMode,
    bar_freshness_profile,
    validate_freshness,
)
from .health import SourceHealthRegistry
from .observability.planned import (
    record_batch_chunks,
    record_cache_event,
    record_planned_query,
    record_provider_health,
)
from .providers import PROVIDERS
from .query import QueryPlan, QueryPlanner, QuerySpec
from .semantic_cache import SemanticQueryCache
from .service import FreshnessEvidence, QueryResult, ResultMeta
from .service import UnifiedMarketDataService as ProviderCoreService

__all__ = ["UnifiedMarketDataService", "ProviderCoreService", "BatchResult", "market_data"]

_LOG = logging.getLogger(__name__)


class UnifiedMarketDataService(ProviderCoreService):
    """Provider-bound service with canonical planning and coordination."""

    def __init__(
        self,
        *args: Any,
        planner: QueryPlanner | None = None,
        default_provider: str | None = None,
        singleflight: SingleFlight | None = None,
        query_cache: Any | None = None,
        disk_cache_path: str | None = None,
        cache_enabled: bool | None = None,
        failure_policy: FailurePolicy | None = None,
        health: SourceHealthRegistry | None = None,
        default_deadline_ms: int = 5000,
        **kwargs: Any,
    ) -> None:
        if default_deadline_ms <= 0:
            raise ValueError("default_deadline_ms must be > 0")
        if planner is not None and default_provider is not None:
            raise ValueError("planner 与 default_provider 不能同时提供：默认 Provider 必须只有一个真相源")
        if query_cache is not None and disk_cache_path is not None:
            raise ValueError("query_cache 与 disk_cache_path 不能同时提供：缓存所有权必须唯一")

        from .config import get_config

        self.config_snapshot = get_config()
        super().__init__(*args, **kwargs)

        if planner is not None:
            self.planner = planner
        else:
            configured_provider = (
                self.config_snapshot.sources.default_provider
                if default_provider is None
                else default_provider
            )
            self.planner = QueryPlanner(default_provider=configured_provider)

        self.singleflight = singleflight if singleflight is not None else SingleFlight()
        self.failure_policy = failure_policy if failure_policy is not None else DEFAULT_FAILURE_POLICY
        self.health = health if health is not None else SourceHealthRegistry()
        self.default_deadline_ms = int(default_deadline_ms)
        self.cache_errors = 0

        self._owns_query_cache = query_cache is None
        if query_cache is not None:
            self.query_cache = query_cache
            self.cache_enabled = True if cache_enabled is None else bool(cache_enabled)
        else:
            self.cache_enabled = (
                bool(self.config_snapshot.cache.enabled)
                if cache_enabled is None
                else bool(cache_enabled)
            )
            l1 = SemanticQueryCache(max_entries=self.config_snapshot.cache.max_entries)
            use_disk = disk_cache_path is not None or self.config_snapshot.cache.backend == "disk"
            if self.cache_enabled and use_disk:
                path = disk_cache_path
                if path is None:
                    path = str(
                        Path(self.config_snapshot.cache.directory).expanduser()
                        / "semantic-v1.sqlite3"
                    )
                l2 = SQLiteSemanticQueryCache(path)
                if l2.enabled:
                    self.query_cache = TieredSemanticQueryCache(l1, l2)
                else:
                    self.cache_errors += l2.errors
                    self.query_cache = l1
            else:
                self.query_cache = l1

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._owns_query_cache:
                close = getattr(self.query_cache, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        _LOG.exception("semantic cache close failed")

    def compile(self, spec: QuerySpec) -> QueryPlan:
        return self.planner.compile(spec)

    def query(self, spec: QuerySpec, *, with_meta: bool = True) -> Any:
        return self._execute_plan(self.compile(spec), with_meta=with_meta)

    def query_many(
        self,
        specs: Sequence[QuerySpec],
        *,
        with_meta: bool = True,
    ) -> list[Any]:
        """Execute multiple canonical queries without inventing provider fallback.

        Exact duplicate semantic queries with the same deadline and cache-age
        policy are executed once within this call. Fan-out uses defensive deep
        copies so mutable Quote/Bar models and nested ``extra``/book objects are
        never shared between logically independent results.
        """
        results: list[Any] = []
        memo: dict[tuple[str, int, bool, float | None], Any] = {}
        for spec in specs:
            plan = self.compile(spec)
            key = (
                plan.fingerprint.value,
                plan.spec.deadline_ms,
                with_meta,
                plan.spec.max_age,
            )
            if key in memo:
                results.append(self._clone_output(memo[key]))
                continue
            value = self._execute_plan(plan, with_meta=with_meta)
            memo[key] = value
            results.append(value)
        return results

    @staticmethod
    def _clone_output(value: Any) -> Any:
        return copy.deepcopy(value)

    def _execute_plan(self, plan: QueryPlan, *, with_meta: bool) -> Any:
        started = time.perf_counter()
        try:
            if plan.spec.capability == "quotes":
                if plan.spec.allow_partial:
                    result = self._execute_quote_batch_plan(plan)
                else:
                    result = self._execute_quotes_plan(plan, with_meta=with_meta)
            elif plan.spec.capability == "bars":
                result = self._execute_bars_plan(plan, with_meta=with_meta)
            else:
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
            record_planned_query(
                provider=plan.provider,
                channel=plan.channel,
                capability=plan.spec.capability,
                status="error",
                duration=time.perf_counter() - started,
            )
            raise
        except Exception as exc:
            record_planned_query(
                provider=plan.provider,
                channel=plan.channel,
                capability=plan.spec.capability,
                status="internal_error",
                duration=time.perf_counter() - started,
            )
            raise InternalError(
                "planned query 未处理异常",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "query_id": plan.fingerprint.value,
                    "phase": "execution",
                    "fallback": False,
                    "cause_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc
        except BaseException:
            record_planned_query(
                provider=plan.provider,
                channel=plan.channel,
                capability=plan.spec.capability,
                status="internal_error",
                duration=time.perf_counter() - started,
            )
            raise

        status = "partial" if isinstance(result, BatchResult) and result.partial else "ok"
        record_planned_query(
            provider=plan.provider,
            channel=plan.channel,
            capability=plan.spec.capability,
            status=status,
            duration=time.perf_counter() - started,
        )
        return result

    def _health_before(self, plan: QueryPlan) -> None:
        try:
            self.health.before_request(plan.provider, plan.channel, plan.spec.capability)
        except BaseException:
            record_provider_health(
                provider=plan.provider,
                channel=plan.channel,
                capability=plan.spec.capability,
                healthy=False,
            )
            raise

    def _health_success(self, plan: QueryPlan) -> None:
        self.health.record_success(plan.provider, plan.channel, plan.spec.capability)
        record_provider_health(
            provider=plan.provider,
            channel=plan.channel,
            capability=plan.spec.capability,
            healthy=True,
        )

    def _health_failure(self, plan: QueryPlan, exc: BaseException) -> None:
        state = self.health.record_failure(
            plan.provider,
            plan.channel,
            plan.spec.capability,
            exc,
            penalize=self.health.should_penalize(exc),
        )
        record_provider_health(
            provider=plan.provider,
            channel=plan.channel,
            capability=plan.spec.capability,
            healthy=not state.circuit_open,
        )

    def _cache_enabled(self, plan: QueryPlan) -> bool:
        return (
            self.cache_enabled
            and not plan.spec.allow_partial
            and plan.spec.max_age is not None
            and plan.spec.max_age > 0
        )

    def _cache_layer(self) -> str:
        if isinstance(self.query_cache, TieredSemanticQueryCache):
            return "tiered"
        if isinstance(self.query_cache, SemanticQueryCache):
            return "memory"
        return "custom"

    @staticmethod
    def _expected_freshness_mode(plan: QueryPlan) -> FreshnessMode | None:
        if plan.spec.capability == "quotes":
            return FreshnessMode.DIRECT_SNAPSHOT
        if plan.spec.capability == "bars":
            return (
                FreshnessMode.HISTORICAL_CLOSED
                if plan.spec.start
                else FreshnessMode.CURRENT_SERIES
            )
        return None

    @classmethod
    def _direct_provenance_matches(cls, plan: QueryPlan, result: QueryResult[Any]) -> bool:
        meta = result.meta
        freshness = meta.freshness
        status = meta.freshness_status
        expected_mode = cls._expected_freshness_mode(plan)
        expected_currentness = expected_mode is not FreshnessMode.HISTORICAL_CLOSED
        return (
            meta.provider == plan.provider
            and meta.channel == plan.channel
            and meta.capability == plan.spec.capability
            and meta.real
            and not meta.fallback
            and freshness.real
            and freshness.origin == "direct"
            and not freshness.cache_hit
            and not freshness.replay
            and not freshness.synthetic
            and meta.observed_at_ns == freshness.observed_at_ns
            and status is not None
            and status.verified
            and status.mode is expected_mode
            and status.currentness_verified is expected_currentness
            and status.provider_timestamp == freshness.provider_timestamp
        )

    @classmethod
    def _validate_direct_result(cls, plan: QueryPlan, result: QueryResult[Any]) -> None:
        if cls._direct_provenance_matches(plan, result):
            return
        meta = result.meta
        status = meta.freshness_status
        expected_mode = cls._expected_freshness_mode(plan)
        raise IntegrityViolation(
            "Provider direct result provenance 与 QueryPlan 不一致",
            context={
                "provider": plan.provider,
                "channel": plan.channel,
                "capability": plan.spec.capability,
                "phase": "provider_result_contract",
                "expected_freshness_mode": expected_mode.value if expected_mode else None,
                "actual_provider": meta.provider,
                "actual_channel": meta.channel,
                "actual_capability": meta.capability,
                "actual_freshness_mode": status.mode.value if status is not None else None,
                "actual_origin": meta.freshness.origin,
                "actual_cache_hit": meta.freshness.cache_hit,
                "actual_real": meta.real,
                "actual_fallback": meta.fallback,
                "fallback": False,
                "provider_switch_allowed": False,
            },
        )

    @classmethod
    def _cache_provenance_matches(cls, plan: QueryPlan, result: QueryResult[Any]) -> bool:
        return cls._direct_provenance_matches(plan, result)

    @staticmethod
    def _as_cache_hit(
        plan: QueryPlan,
        result: QueryResult[Any],
        *,
        max_age: float,
    ) -> QueryResult[Any]:
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
        historical_bars = meta.capability == "bars" and bool(plan.spec.start)
        if meta.capability == "bars":
            base_profile = bar_freshness_profile(
                meta.provider,
                meta.channel,
                plan.spec.period or "day",
                historical=historical_bars,
            )
        else:
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
            require_live=not historical_bars,
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
        layer = self._cache_layer()
        max_age = float(plan.spec.max_age if plan.spec.max_age is not None else 0.0)
        try:
            cached = self.query_cache.get(plan.fingerprint.value, max_age=max_age)
            if cached is None:
                record_cache_event(layer=layer, status="miss")
                return None
            if not isinstance(cached, QueryResult):
                self.query_cache.invalidate(plan.fingerprint.value)
                record_cache_event(layer=layer, status="invalid")
                return None
            if not self._cache_provenance_matches(plan, cached):
                self.query_cache.invalidate(plan.fingerprint.value)
                record_cache_event(layer=layer, status="invalid")
                _LOG.warning(
                    "semantic cache provenance mismatch; treating as miss: "
                    "expected=%s/%s/%s got=%s/%s/%s",
                    plan.provider,
                    plan.channel,
                    plan.spec.capability,
                    cached.meta.provider,
                    cached.meta.channel,
                    cached.meta.capability,
                )
                return None
            try:
                result = self._as_cache_hit(plan, cached, max_age=max_age)
            except TdxError:
                self.query_cache.invalidate(plan.fingerprint.value)
                record_cache_event(layer=layer, status="stale")
                return None
            record_cache_event(layer=layer, status="hit")
            return result
        except Exception as exc:
            self.cache_errors += 1
            record_cache_event(layer=layer, status="error")
            _LOG.warning("semantic cache read failed; treating as miss: %s", exc)
            return None

    def _cache_put(self, plan: QueryPlan, result: QueryResult[Any]) -> None:
        if not self._cache_enabled(plan):
            return
        layer = self._cache_layer()
        try:
            self.query_cache.put(plan.fingerprint.value, result)
            record_cache_event(layer=layer, status="write")
        except Exception as exc:
            self.cache_errors += 1
            record_cache_event(layer=layer, status="error")
            _LOG.warning("semantic cache write failed; ignoring optimization failure: %s", exc)

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

    @staticmethod
    def _batch_limit(plan: QueryPlan) -> int | None:
        channel = PROVIDERS.get(plan.provider).channel(plan.channel)
        return channel.batch_limit_for(plan.spec.capability)

    def _quote_chunks(self, plan: QueryPlan, batch: BatchPlan) -> tuple[tuple[str, ...], ...]:
        chunks = BatchPlanner.chunks(batch.unique, self._batch_limit(plan))
        record_batch_chunks(
            provider=plan.provider,
            channel=plan.channel,
            capability="quotes",
            chunks=len(chunks),
        )
        return chunks

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
        chunks = self._quote_chunks(plan, batch)

        def fetch() -> QueryResult[list[Quote]]:
            plan.budget.begin_attempt()
            self._health_before(plan)
            rows: list[Quote] = []
            meta: ResultMeta | None = None
            try:
                for index, chunk in enumerate(chunks):
                    plan.budget.ensure_remaining(f"provider_chunk_{index}")
                    result = super(UnifiedMarketDataService, self).quotes(
                        list(chunk), provider=plan.provider, with_meta=True
                    )
                    if not isinstance(result, QueryResult):
                        raise RuntimeError(
                            "ProviderCoreService.quotes(with_meta=True) contract violated"
                        )
                    self._validate_direct_result(plan, result)
                    rows.extend(result.data)
                    meta = result.meta
                    plan.budget.ensure_remaining(f"provider_chunk_{index}_response")
                if meta is None:
                    raise RuntimeError("quotes execution produced no Provider metadata")
                coordinated = QueryResult(
                    data=self._align_quote_batch(batch, rows),
                    meta=meta,
                )
            except BaseException as exc:
                self._health_failure(plan, exc)
                raise
            self._health_success(plan)
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
        """Return auditable per-symbol errors and never cache partial results."""
        batch = BatchPlanner.symbols(list(plan.spec.symbols))
        chunks = self._quote_chunks(plan, batch)

        def fetch() -> BatchResult[Quote]:
            plan.budget.begin_attempt()
            self._health_before(plan)
            by_symbol: dict[str, Quote] = {}
            errors: dict[str, ErrorEnvelope] = {}
            meta: ResultMeta | None = None
            health_recorded_failure = False

            for index, chunk in enumerate(chunks):
                chunk_batch = BatchPlanner.symbols(list(chunk))
                attempted = False
                try:
                    plan.budget.ensure_remaining(f"provider_chunk_{index}")
                    attempted = True
                    result = super(UnifiedMarketDataService, self).quotes(
                        list(chunk), provider=plan.provider, with_meta=True
                    )
                    if not isinstance(result, QueryResult):
                        raise RuntimeError(
                            "ProviderCoreService.quotes(with_meta=True) contract violated"
                        )
                    self._validate_direct_result(plan, result)
                    meta = result.meta
                    plan.budget.ensure_remaining(f"provider_chunk_{index}_response")
                    chunk_map = self._quote_map(chunk_batch, list(result.data))
                except TdxError as exc:
                    self._health_failure(plan, exc)
                    health_recorded_failure = True
                    root = to_error_envelope(
                        exc,
                        provider=plan.provider,
                        channel=plan.channel,
                        capability="quotes",
                        query_id=plan.fingerprint.value,
                    )
                    root_summary = {
                        "code": root.code,
                        "type": root.type,
                        "message": root.message,
                        "phase": root.phase,
                        "chunk_index": index,
                    }
                    current_status = "failed" if attempted else "not_attempted"
                    for symbol in chunk:
                        context = dict(root.context)
                        context.update(
                            {
                                "symbol": symbol,
                                "chunk_index": index,
                                "attempted": attempted,
                                "batch_status": current_status,
                                "original_error": root_summary,
                            }
                        )
                        errors[symbol] = replace(root, partial=True, context=context)

                    for later_index, later_chunk in enumerate(chunks[index + 1 :], index + 1):
                        for symbol in later_chunk:
                            blocked = SourceUnavailable(
                                "前序 Provider chunk 失败，本标的未继续请求",
                                context={
                                    "provider": plan.provider,
                                    "channel": plan.channel,
                                    "capability": "quotes",
                                    "query_id": plan.fingerprint.value,
                                    "phase": "batch",
                                    "symbol": symbol,
                                    "chunk_index": later_index,
                                    "attempted": False,
                                    "batch_status": "not_attempted",
                                    "blocked_by": root_summary,
                                    "partial": True,
                                    "fallback": False,
                                    "retry_same_provider": False,
                                    "terminal": True,
                                    "provider_switch_allowed": False,
                                },
                            )
                            errors[symbol] = to_error_envelope(
                                blocked,
                                provider=plan.provider,
                                channel=plan.channel,
                                capability="quotes",
                                query_id=plan.fingerprint.value,
                            )
                    break
                except BaseException as exc:
                    self._health_failure(plan, exc)
                    raise

                by_symbol.update(chunk_map)
                missing = [symbol for symbol in chunk if symbol not in chunk_map]
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
                            "chunk_index": index,
                            "attempted": True,
                            "batch_status": "missing",
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

            if errors and not health_recorded_failure:
                self._health_failure(
                    plan,
                    SourceUnavailable(
                        "Provider 批量行情返回部分缺失",
                        context={"missing_symbols": list(errors), "partial": True},
                    ),
                )
            elif not errors:
                self._health_success(plan)

            items = tuple(by_symbol[symbol] for symbol in batch.original if symbol in by_symbol)
            return BatchResult(
                items=items,
                errors=errors,
                requested=batch.original,
                partial=bool(errors),
                meta=meta,
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
            self._health_before(plan)
            try:
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
                self._validate_direct_result(plan, result)
                plan.budget.ensure_remaining("provider_response")
            except BaseException as exc:
                self._health_failure(plan, exc)
                raise
            self._health_success(plan)
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
