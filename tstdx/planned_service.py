# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Query-planned public market-data service.

``tstdx.service.UnifiedMarketDataService`` remains the Provider execution core.
This module layers deterministic QuerySpec/QueryPlan compilation, one total
execution deadline, batch de-duplication/chunking, SingleFlight, dynamic health
and semantic cache V2 without reimplementing Provider adapters or fallback.
"""

from __future__ import annotations

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
from .errors import IntegrityViolation, SourceUnavailable, TdxError, ValidationError
from .execution import BatchPlan, BatchPlanner, SingleFlight
from .failure import DEFAULT_FAILURE_POLICY, FailurePolicy
from .freshness import FRESHNESS, validate_freshness
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
            raise ValueError("planner ä¸Ž default_provider ä¸èƒ½åŒæ—¶æä¾›ï¼šé»˜è®¤ Provider å¿…é¡»åªæœ‰ä¸€ä¸ªçœŸç›¸æº")
        if query_cache is not None and disk_cache_path is not None:
            raise ValueError("query_cache ä¸Ž disk_cache_path ä¸èƒ½åŒæ—¶æä¾›ï¼šç¼“å­˜æ‰€æœ‰æƒå¿…é¡»å”¯ä¸€")

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

        Exact duplicate semantic queries with the same deadline are executed once
        within this call. Result containers are copied on fan-out so callers do
        not share the same list/dict container by accident.
        """
        results: list[Any] = []
        memo: dict[tuple[str, int, bool], Any] = {}
        for spec in specs:
            plan = self.compile(spec)
            key = (plan.fingerprint.value, plan.spec.deadline_ms, with_meta)
            if key in memo:
                results.append(self._clone_output(memo[key]))
                continue
            value = self._execute_plan(plan, with_meta=with_meta)
            memo[key] = value
            results.append(value)
        return results

    @staticmethod
    def _clone_output(value: Any) -> Any:
        if isinstance(value, QueryResult):
            data = list(value.data) if isinstance(value.data, list) else value.data
            return QueryResult(data=data, meta=value.meta)
        if isinstance(value, BatchResult):
            return BatchResult(
                items=tuple(value.items),
                errors=dict(value.errors),
                requested=tuple(value.requested),
                partial=value.partial,
                meta=value.meta,
            )
        if isinstance(value, list):
            return list(value)
        return value

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
                    f"ç»Ÿä¸€ query() å°šæœªæŽ¥å…¥ capability {plan.spec.capability!r}",
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

    def _health_failure(self, plan, exc: BaseException) -> None:
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
            try:
                result = self._as_cache_hit(cached, max_age=max_age)
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
            _LOG.warning ("semantic cache write failed; ignoring optimization failure: %s", exc)

    @staticmethod
    def _quote_map(batch: BatchPlan, rows: Sequence[Quote]) -> dict[str, Quote]:
        expected = set(batch.unique)
        by_symbol: dict[str, Quote] = {}
        for quote in rows:
            try:
                symbol = normalize_symbol(str(quote.code))
            except Exception as exc:
                raise IntegrityViolation(
                   "ä¾›åº” ªê-xƒ–N7–êS–2–B¯š^ƒšÎW–öK’â–2[šZžj½‘”ˆ°(€€€€€€€€€€€€€€€€€€€½¹Ñ•áÐõì‰½‘”ˆèÍÑÈ¡ÅÕ½Ñ”¹½‘”¥ô°(€€€€€€€€€€€€€€€€€€€…ÕÍ”õ•áŒ°(€€€€€€€€€€€€€€€€¤™É½´•áŒ(€€€€€€€€€€€¥˜Íåµ‰½°¹½Ð¥¸•áÁ•Ñ•è(€€€€€€€€€€€€€€€É…¥Í”%¹Ñ•É¥ÑåY¥½±…Ñ¥½¸ (€€€€€€€€€€€€€€€€€€€€‹’úo–êP‚«¨µâY8Þ[©NXÈ^Y
¾iÊ®h«îy¨B"À¢6öçFW‡C×²'7–Ö&öÂ#¢7–Ö&öÂÂ'&WVW7FVB#¢Æ—7B†&F6‚çVæ—VR—ÒÀ¢¢–b7–Ö&öÂ–â'•÷7–Ö&öÃ ¢&—6R–çFVw&—G•f–öÆF–öâ€¢.Ké¾[©RV÷FRY8Þ[©NXÈ^Y
¾™(ÎZHÞj~y¨B"À¢6öçFW‡C×²'7–Ö&öÂ#¢7–Ö&öÇÒÀ¢¢'•÷7–Ö&öÅ·7–Ö&öÅÒÒV÷FP¢&WGW&â'•÷7–Ö&öÀ ¢6Æ76ÖWF†ö@¢FVböÆ–vå÷V÷FUö&F6‚†6Ç2Â&F6ƒ¢&F6…ÆâÂ&÷w3¢6WVVæ6UµV÷FUÒ’ÓâÆ—7EµV÷FUÓ ¢'•÷7–Ö&öÂÒ6Ç2å÷V÷FUöÖ†&F6‚Â&÷w2¢Ö—76–ærÒ·7–Ö&öÂf÷"7–Ö&öÂ–â&F6‚çVæ—VR–b7–Ö&öÂæ÷B–â'•÷7–Ö&öÅÐ¢–bÖ—76–æs ¢&—6R6÷W&6UVæf–Æ&ÆR€¢%&÷f–FW"h›ž˜xþŠÎh8^{Ë®[	Šû~k.j~y¨B"À¢6öçFW‡C×°¢&Ö—76–æu÷7–Ö&öÇ2#¢Ö—76–ærÀ¢'&WVW7FVE÷Væ—VR#¢ÆVâ†&F6‚çVæ—VR’À¢'&V6V—fVE÷Væ—VR#¢ÆVâ†'•÷7–Ö&öÂ’À¢''F–Â#¢&ööÂ†'•÷7–Ö&öÂ’À¢ÒÀ¢¢&WGW&â¶'•÷7–Ö&öÅ·7–Ö&öÅÒf÷"7–Ö&öÂ–â&F6‚æ÷&–v–æÅÐ ¢7FF–6ÖWF†ö@¢FVbö&F6…öÆ–Ö—B‡Æã¢VW'•Æâ’Óâ–çBÂæöæS ¢6†ææVÂÒ$õd”DU%2ævWB‡Æâç&÷f–FW"’æ6†ææVÂ‡Æâæ6†ææVÂ¢&WGW&â6†ææVÂæ&F6…öÆ–Ö—Eöf÷"‡Æâç7V2æ6&–Æ—G’ ¢FVb÷V÷FUö6‡Væ·2‡6VÆbÂÆã¢VW'•ÆâÂ&F6ƒ¢&F6…Æâ’ÓâGWÆU·GWÆU·7G"ÂââåÒÂââåÓ ¢6‡Væ·2Ò&F6…ÆææW"æ6‡Væ·2†&F6‚çVæ—VRÂ6VÆbåö&F6…öÆ–Ö—B‡Æâ’¢&V6÷&Eö&F6…ö6‡Væ·2€¢&÷f–FW#×Æâç&÷f–FW"À¢6†ææVÃ×Æâæ6†ææVÂÀ¢6&–Æ—G“Ò'V÷FW2"À¢6‡Væ·3ÖÆVâ†6‡Væ·2’À¢¢&WGW&â6‡Væ·0 ¢FVbV÷FW2€¢6VÆbÀ¢7–Ö&öÇ3¢7G"Â6WVVæ6U·7G%ÒÀ¢¢À¢&÷f–FW#¢7G"ÂæöæRÒæöæRÀ¢6÷W&6S¢7G"ÂæöæRÒæöæRÀ¢v—F…öÖWF¢&ööÂÒfÇ6RÀ¢ÆÆ÷u÷'F–Ã¢&ööÂÒfÇ6RÀ¢FVFÆ–æUö×3¢–çBÂæöæRÒæöæRÀ¢Ö…övS¢fÆöBÂæöæRÒæöæRÀ¢ÆÆ÷u÷7FÆS¢&ööÂÒfÇ6RÀ¢’ÓâÆ—7EµV÷FUÒÂVW'•&W7VÇE¶Æ—7EµV÷FUÕÒÂ&F6…&W7VÇEµV÷FUÓ ¢6WÒ‡7–Ö&öÇ2Â’–b—6–ç7Fæ6R‡7–Ö&öÇ2Â7G"’VÇ6RGWÆR‡7–Ö&öÇ2¢7V2ÒVW'•7V2æ'V–ÆB€¢'V÷FW2"À¢7–Ö&öÇ3×6WÀ¢&÷f–FW#×&÷f–FW"À¢6÷W&6S×6÷W&6RÀ¢ÆÆ÷u÷'F–ÃÖÆÆ÷u÷'F–ÂÀ¢FVFÆ–æUö×3×6VÆbæFVfVÇEöFVFÆ–æUö×2–bFVFÆ–æUö×2—2æöæRVÇ6RFVFÆ–æUö×2À¢Ö…övSÖÖ…övRÀ¢ÆÆ÷u÷7FÆSÖÆÆ÷u÷7FÆRÀ¢¢&WGW&â6VÆbçVW'’‡7V2Âv—F…öÖWF×v—F…öÖWF ¢FVbV÷FW5ö&F6‚€¢6VÆbÀ¢7–Ö&öÇ3¢7G"Â6WVVæ6U·7G%ÒÀ¢¢À¢&÷f–FW#¢7G"ÂæöæRÒæöæRÀ¢6÷W&6S¢7G"ÂæöæRÒæöæRÀ¢FVFÆ–æUö×3¢–çBÂæöæRÒæöæRÀ¢’Óâ&F6…&W7VÇEµV÷FUÓ ¢&W7VÇBÒ6VÆbçV÷FW2€¢7–Ö&öÇ2À¢&÷f–FW#×&÷f–FW"À¢6÷W&6S×6÷W&6RÀ¢ÆÆ÷u÷'F–ÃÕG'VRÀ¢FVFÆ–æUö×3ÖFVFÆ–æUö×2À¢¢–bæ÷B—6–ç7Fæ6R‡&W7VÇBÂ&F6…&W7VÇB“ ¢&—6R'VçF–ÖTW'&÷"‚'V÷FW5ö&F6‚6öçG&7Bf–öÆFVB"¢&WGW&â&W7VÇ@ ¢FVböW†V7WFU÷V÷FW5÷Æâ€¢6VÆbÀ¢Æã¢VW'•ÆâÀ¢¢À¢v—F…öÖWF¢&ööÂÀ¢’ÓâÆ—7EµV÷FUÒÂVW'•&W7VÇE¶Æ—7EµV÷FUÕÓ ¢66†VBÒ6VÆbåö66†UövWB‡Æâ¢–b66†VB—2æ÷BæöæS ¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚&66†U÷&VB"¢&WGW&â66†VB–bv—F…öÖWFVÇ6R66†VBæFF¢&F6‚Ò&F6…ÆææW"ç7–Ö&öÇ2†Æ—7B‡Æâç7V2ç7–Ö&öÇ2’¢6‡Væ·2Ò6VÆbå÷V÷FUö6‡Væ·2‡ÆâÂ&F6‚ ¢FVbfWF6‚‚’ÓâVW'•&W7VÇE¶Æ—7EµV÷FUÕÓ ¢Æâæ'VFvWBæ&Vv–åöGFV×B‚¢6VÆbåö†VÇF…ö&Vf÷&R‡Æâ¢&÷w3¢Æ—7EµV÷FUÒÒµÐ¢ÖWF¢&W7VÇDÖWFÂæöæRÒæöæP¢G'“ ¢f÷"–æFW‚Â6‡Væ²–âVçVÖW&FR†6‡Væ·2“ ¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær†b'&÷f–FW%ö6‡Væµ÷¶–æFW‡Ò"¢&W7VÇBÒ7WW"…Væ–f–VDÖ&¶WDFF6W'f–6RÂ6VÆb’çV÷FW2€¢Æ—7B†6‡Væ²’Â&÷f–FW#×Æâç&÷f–FW"Âv—F…öÖWFÕG'VP¢¢–bæ÷B—6–ç7Fæ6R‡&W7VÇBÂVW'•&W7VÇB“ ¢&—6R'VçF–ÖTW'&÷"€¢%&÷f–FW$6÷&U6W'f–6RçV÷FW2‡v—F…öÖWFÕG'VR’6öçG&7Bf–öÆFVB ¢¢&÷w2æW‡FVæB‡&W7VÇBæFF¢ÖWFÒ&W7VÇBæÖWF¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær†b'&÷f–FW%ö6‡Væµ÷¶–æFW‡Õ÷&W7öç6R"¢–bÖWF—2æöæS ¢&—6R'VçF–ÖTW'&÷"‚'V÷FW2W†V7WF–öâ&öGV6VBæò&÷f–FW"ÖWFFF"¢6ö÷&F–æFVBÒVW'•&W7VÇB€¢FF×6VÆbåöÆ–vå÷V÷FUö&F6‚†&F6‚Â&÷w2’À¢ÖWFÖÖWFÀ¢¢W†6WB&6TW†6WF–öâ2W†3 ¢6VÆbåö†VÇF…öf–ÇW&R‡ÆâÂW†2¢&—6P¢6VÆbåö†VÇF…÷7V66W72‡Æâ¢6VÆbåö66†U÷WB‡ÆâÂ6ö÷&F–æFVB¢&WGW&â6ö÷&F–æFV@ ¢&W7VÇBÒ6VÆbç6–ævÆVfÆ–v‡BæFò€¢Æâæf–ævW'&–çBçfÇVRÀ¢fWF6‚À¢F–ÖV÷WC×Æâæ'VFvWBç&VÖ–æ–æu÷2‚’À¢¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚'6W&–Æ—¦R"¢&WGW&â&W7VÇB–bv—F…öÖWFVÇ6R&W7VÇBæFF ¢FVböW†V7WFU÷V÷FUö&F6…÷Æâ‡6VÆbÂÆã¢VW'•Æâ’Óâ&F6…&W7VÇEµV÷FUÓ ¢""%&WGW&âVF—F&ÆRW"×7–Ö&öÂW'&÷'2æBæWfW"66†R'F–Â&W7VÇG2â"" ¢&F6‚Ò&F6…ÆææW"ç7–Ö&öÇ2†Æ—7B‡Æâç7V2ç7–Ö&öÇ2’¢6‡Væ·2Ò6VÆbå÷V÷FUö6‡Væ·2‡ÆâÂ&F6‚ ¢FVbfWF6‚‚’Óâ&F6…&W7VÇEµV÷FUÓ ¢Æâæ'VFvWBæ&Vv–åöGFV×B‚¢6VÆbåö†VÇF…ö&Vf÷&R‡Æâ¢'•÷7–Ö&öÃ¢F–7E·7G"ÂV÷FUÒÒ·Ð¢W'&÷'3¢F–7E·7G"ÂW'&÷$VçfVÆ÷UÒÒ·Ð¢ÖWF¢&W7VÇDÖWFÂæöæRÒæöæP¢†VÇF…÷&V6÷&FVEöf–ÇW&RÒfÇ6P ¢f÷"–æFW‚Â6‡Væ²–âVçVÖW&FR†6‡Væ·2“ ¢6‡Væµö&F6‚Ò&F6…ÆææW"ç7–Ö&öÇ2†Æ—7B†6‡Væ²’¢GFV×FVBÒfÇ6P¢G'“ ¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær†b'&÷f–FW%ö6‡Væµ÷¶–æFW‡Ò"¢GFV×FVBÒG'VP¢&W7VÇBÒ7WW"…Væ–f–VDÖ&¶WDFF6W'f–6RÂ6VÆb’çV÷FW2€¢Æ—7B†6‡Væ²’Â&÷f–FW#×Æâç&÷f–FW"Âv—F…öÖWFÕG'VP¢¢–bæ÷B—6–ç7Fæ6R‡&W7VÇBÂVW'•&W7VÇB“ ¢&—6R'VçF–ÖTW'&÷"€¢%&÷f–FW$6÷&U6W'f–6RçV÷FW2‡v—F…öÖWFÕG'VR’6öçG&7Bf–öÆFVB ¢¢ÖWFÒ&W7VÇBæÖWF¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær†b'&÷f–FW%ö6‡Væµ÷¶–æFW‡Õ÷&W7öç6R"¢6‡VæµöÖÒ6VÆbå÷V÷FUöÖ†6‡Væµö&F6‚ÂÆ—7B‡&W7VÇBæFF’¢W†6WBFG„W'&÷"2W†3 ¢6VÆbåö†VÇF…öf–ÇW&R‡ÆâÂW†2¢†VÇF…÷&V6÷&FVEöf–ÇW&RÒG'VP¢&ö÷BÒFõöW'&÷%öVçfVÆ÷R€¢W†2À¢&÷f–FW#×Æâç&÷f–FW"À¢6†ææVÃ×Æâæ6†ææVÂÀ¢6&–Æ—G“Ò'V÷FW2"À¢VW'•ö–C×Æâæf–ævW'&–çBçfÇVRÀ¢¢&ö÷E÷7VÖÖ'’Ò°¢&6öFR#¢&ö÷Bæ6öFRÀ¢'G—R#¢&ö÷BçG—RÀ¢&ÖW76vR#¢&ö÷BæÖW76vRÀ¢'†6R#¢&ö÷Bç†6RÀ¢&6‡Væµö–æFW‚#¢–æFW‚À¢Ð¢7W'&VçE÷7FGW2Ò&f–ÆVB"–bGFV×FVBVÇ6R&æ÷EöGFV×FVB ¢f÷"7–Ö&öÂ–â6‡Væ³ ¢6öçFW‡BÒF–7B‡&ö÷Bæ6öçFW‡B¢6öçFW‡BçWFFR€¢°¢'7–Ö&öÂ#¢7–Ö&öÂÀ¢&6‡Væµö–æFW‚#¢–æFW‚À¢&GFV×FVB#¢GFV×FVBÀ¢&&F6…÷7FGW2#¢7W'&VçE÷7FGW2À¢&÷&–v–æÅöW'&÷"#¢&ö÷E÷7VÖÖ'’À¢Ð¢¢W'&÷'5·7–Ö&öÅÒÒ&WÆ6R‡&ö÷BÂ'F–ÃÕG'VRÂ6öçFW‡CÖ6öçFW‡B ¢f÷"ÆFW%ö–æFW‚ÂÆFW%ö6‡Væ²–âVçVÖW&FR†6‡Væ·5¶–æFW‚²¥ÒÂ–æFW‚²“ ¢f÷"7–Ö&öÂ–âÆFW%ö6‡Væ³ ¢&Æö6¶VBÒ6÷W&6UVæf–Æ&ÆR€¢.X˜Þ[¨ò&÷f–FW"6‡Væ²ZK‹J^ûÈÎiÊÎj~y¨NiÊ®{º~{ºÞ‹ù¾ŠÎŠû~k""À¢6öçFW‡C×°¢'&÷f–FW"#¢Æâç&÷f–FW"À¢&6†ææVÂ#¢Æâæ6†ææVÂÀ¢&6&–Æ—G’#¢'V÷FW2"À¢'VW'•ö–B#¢Æâæf–ævW'&–çBçfÇVRÀ¢'†6R#¢&&F6‚"À¢'7–Ö&öÂ#¢7–Ö&öÂÀ¢&6‡Væµö–æFW‚#¢ÆFW%ö–æFW‚À¢&GFV×FVB#¢fÇ6RÀ¢&&F6…÷7FGW2#¢&æ÷EöGFV×FVB"À¢&&Æö6¶VEö'’#¢&ö÷E÷7VÖÖ'’À¢''F–Â#¢G'VRÀ¢&fÆÆ&6²#¢fÇ6RÀ¢'&WG'•÷6ÖU÷&÷f–FW"#¢fÇ6RÀ¢'FW&Ö–æÂ#¢G'VRÀ¢'&÷f–FW%÷7v—F6…öÆÆ÷vVB#¢fÇ6RÀ¢ÒÀ¢¢W'&÷'5·7–Ö&öÅÒÒFõöW'&÷%öVçfVÆ÷R€¢&Æö6¶VBÀ¢&÷f–FW#×Æâç&÷f–FW"À¢6†ææVÃ×Æâæ6†ææVÂÀ¢6&–Æ—G“Ò'V÷FW2"À¢VW'•ö–C×Æâæf–ævW'&–çBçfÇVRÀ¢¢'&V°¢W†6WB&6TW†6WF–öâ2W†3 ¢6VÆbåö†VÇF…öf–ÇW&R‡ÆâÂW†2¢&—6P ¢'•÷7–Ö&öÂçWFFR†6‡VæµöÖ¢Ö—76–ærÒ·7–Ö&öÂf÷"7–Ö&öÂ–â6‡Væ²–b7–Ö&öÂæ÷B–â6‡VæµöÖÐ¢f÷"7–Ö&öÂ–âÖ—76–æs ¢W†2Ò6÷W&6UVæf–Æ&ÆR€¢%&÷f–FW"h‹ž˜xþŠÎh8^{Ë®[	Šû~k.j~y¨B"À¢6öçFW‡C×°¢'&÷f–FW"#¢Æâç&÷f–FW"À¢&6†ææVÂ#¢Æâæ6†ææVÂÀ¢&6&–Æ—G’#¢'V÷FW2"À¢'VW'•ö–B#¢Æâæf–ævW'&–çBçfÇVRÀ¢'†6R#¢&æ÷&ÖÆ—¦R"À¢'7–Ö&öÂ#¢7–Ö&öÂÀ¢&6‡Væµö–æFW‚#¢–æFW‚À¢&GFV×FVB#¢G'VRÀ¢&&F6…÷7FGW2#¢&Ö—76–ær"À¢''F–Â#¢G'VRÀ¢&fÆÆ&6²#¢fÇ6RÀ¢'&WG'•÷6ÖU÷&÷f–FW"#¢fÇ6RÀ¢'FW&Ö–æÂ#¢G'VRÀ¢'&÷f–FW%÷7v—F6…öÆÆ÷vVB#¢fÇ6RÀ¢ÒÀ¢¢W'&÷'5·7–Ö&öÅÒÒFõöW'&÷%öVçfVÆ÷R€¢W†2À¢&÷f–FW#×Æâç&÷f–FW"À¢6†ææVÃ×Æâæ6†ææVÂÀ¢6&–Æ—G“Ò'V÷FW2"À¢VW'•ö–C×Æâæf–ævW'&–çBçfÇVRÀ¢ ¢–bW'&÷'2æBæ÷B†VÇF…÷&V6÷&FVEöf–ÇW&S ¢6VÆbåö†VÇF…öf–ÇW&R€¢ÆâÀ¢6÷W&6UVæf–Æ&ÆR€¢%&÷f–FW"h›ž˜xþŠÎh8^Y¹îY¹î˜:ŽXˆn{Ë®ZK"À¢6öçFW‡C×²&Ö—76–æu÷7–Ö&öÇ2#¢Æ—7B†W'&÷'2’Â''F–Â#¢G'VWÒÀ¢’À¢¢VÆ–bæ÷BW'&÷'3 ¢6VÆbåö†VÇF…÷7V66W72‡Æâ ¢—FV×2ÒGWÆR†'•÷7–Ö&öÅ·7–Ö&öÅÒf÷"7–Ö&öÂ–â&F6‚æ÷&–v–æÂ–b7–Ö&öÂ–â'•÷7–Ö&öÂ¢&WGW&â&F6…&W7VÇB€¢—FV×3Ö—FV×2À¢W'&÷'3ÖW'&÷'2À¢&WVW7FVCÖ&F6‚æ÷&–v–æÂÀ¢'F–ÃÖ&ööÂ†W'&÷'2’À¢ÖWFÖÖWFÀ¢ ¢&W7VÇBÒ6VÆbç6–ævÆVfÆ–v‡BæFò€¢Æâæf–ævW'&–çBçfÇVRÀ¢fWF6‚À¢F–ÖV÷WC×Æâæ'VFvWBç&VÖ–æu÷2‚’À¢¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚'6W&–Æ—¦R"¢&WGW&â&W7VÇ@ ¢FVb&'2€¢6VÆbÀ¢7–Ö&öÃ¢7G"À¢¢À¢W&–öC¢7G"Ò&F’"À¢6÷VçC¢–çBÒ3#À¢7F'C¢–çBÒÀ¢F§W7C¢7G"Ò""À¢&÷f–FW#¢7G"ÂæöæRÒæöæRÀ¢6÷W&6S¢7G"ÂæöæRÒæöæRÀ¢v—F…öÖWF¢&ööÂÒfÇ6RÀ¢FVFÆ–æUö×3¢–çBÂæöæRÒæöæRÀ¢Ö…övS¢fÆöBÂæöæRÒæöæRÀ¢ÆÆ÷u÷7FÆS¢&ööÂÒfÇ6RÀ¢’ÓâÆ—7E´&%ÒÂVW'•&W7VÇE¶Æ—7E´&%ÕÓ ¢7V2ÒVW'•7V2æ'V–ÆB€¢&&'2"À¢7–Ö&öÇ3Ò‡7–Ö&öÂÂ’À¢&÷f–FW#×&÷f–FW"À¢6÷W&6S×6÷W&6RÀ¢W&–öC×W&–öBÀ¢6÷VçCÖ6÷VçBÀ¢7F'C×7F'BÀ¢F§W7FÖVçCÖF§W7BÀ¢FVFÆ–æUö×3×6VÆbæFVfVÇEöFVFÆ–æUö×2–bFVFÆ–æUö×2—2æöæRVÇ6RFVFÆ–æUö×2À¢Ö…övSÖÖ…övRÀ¢ÆÆ÷u÷7FÆSÖÆÆ÷u÷7FÆRÀ¢¢&WGW&â6VÆbçVW'’‡7V2Âv—F…öÖWF×v—F…öÖWF ¢FVböW†V7WFUö&'5÷Æâ€¢6VÆbÀ¢Æã¢VW'•ÆâÀ¢¢À¢v—F…öÖWF¢&ööÂÀ¢’ÓâÆ—7E´&%ÒÂVW'•&W7VÇE¶Æ—7E´&%ÕÓ ¢66†VBÒ6VÆbåö66†UövWB‡Æâ¢–b66†VB—2æ÷BæöæS ¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚&66†U÷&VB"¢&WGW&â66†VB–bv—F…öÖWFVÇ6R66†VBæFF¢7–Ö&öÂÒÆâç7V2ç7–Ö&öÇ5³Ð ¢FVbfWF6‚‚’ÓâVW'•&W7VÇE¶Æ—7E´&%ÕÓ ¢Æâæ'VFvWBæ&Vv–åöGFV×B‚¢6VÆbåö†VÇF…ö&Vf÷&R‡Æâ¢G'“ ¢&W7VÇBÒ7WW"…Væ–f–VDÖ&¶WDFF6W'f–6RÂ6VÆb’æ&'2€¢7–Ö&öÂÀ¢W&–öC×Æâç7V2çW&–öB÷"&F’"À¢6÷VçC×Æâç7V2æ6÷VçBÀ¢7F'C×Æâç7V2ç7F'BÀ¢F§W7C×Æâç7V2æF§W7FÖVçBÀ¢&÷f–FW#×Æâç&÷f–FW"À¢v—F…öÖWFÕG'VRÀ¢¢–bæ÷B—6–ç7Fæ6R‡&W7VÇBÂVW'•&W7VÇB“ ¢&—6R'VçF–ÖTW'&÷"‚%&÷f–FW$6÷&U6W'f–6Ræ&'2‡v—F…öÖWFÕG'VR’6öçG&7Bf–öÆFVB"¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚'&÷f–FW%÷&W7öç6R"¢W†6WB&6TW†6WF–öâ2W†3 ¢6VÆbåö†VÇF…öf–ÇW&R‡ÆâÂW†2¢&—6P¢6VÆbåö†VÇF…÷7V66W72‡Æâ¢6VÆbåö66†U÷WB‡ÆâÂ&W7VÇB¢&WGW&â&W7VÇ@ ¢&W7VÇBÒ6VÆbç6–ævÆVfÆ–v‡BæFò€¢Æâæf–ævW'&–çBçfÇVRÀ¢fWF6‚À¢F–ÖV÷WC×Æâæ'VFvWBç&VÖ–æ–æu÷2‚’À¢¢Æâæ'VFvWBæVç7W&U÷&VÖ–æ–ær‚'6W&–Æ—¦R"¢&WGW&â&W7VÇB–bv—F…öÖWFVÇ6R&W7VÇBæFF  ¦FVbÖ&¶WEöFF‚¢¦·v&w3¢ç’’ÓâVæ–f–VDÖ&¶WDFF6W'f–6S ¢&WGW&âVæ–f–VDÖ&¶WDFF6W'f–6R‚¢¦·v&w2 