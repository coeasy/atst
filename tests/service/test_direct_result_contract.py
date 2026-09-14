from __future__ import annotations

import time
from typing import Any

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import IntegrityViolation
from tstdx.freshness import FreshnessMode, FreshnessStatus
from tstdx.health import SourceHealthRegistry
from tstdx.planned_service import ProviderCoreService, UnifiedMarketDataService
from tstdx.semantic_cache import SemanticQueryCache
from tstdx.service import FreshnessEvidence, QueryResult, ResultMeta


class Manager:
    def close(self) -> None:
        pass


def _poisoned_result() -> QueryResult[list[Quote]]:
    observed = time.time_ns()
    provider_timestamp = None
    return QueryResult(
        data=[Quote(code="sh600519", price=10.0)],
        meta=ResultMeta(
            provider="sina",
            channel="quote",
            capability="quotes",
            observed_at_ns=observed,
            freshness=FreshnessEvidence(
                origin="direct",
                observed_at_ns=observed,
                provider_timestamp=provider_timestamp,
            ),
            freshness_status=FreshnessStatus(
                verified=True,
                mode=FreshnessMode.DIRECT_SNAPSHOT,
                basis="poisoned-test",
                provider_timestamp=provider_timestamp,
                observed_age_seconds=0.0,
                currentness_verified=True,
            ),
            real=True,
            fallback=False,
        ),
    )


def test_direct_provenance_mismatch_fails_before_health_success_and_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_quotes(self: Any, *args: Any, **kwargs: Any) -> QueryResult[list[Quote]]:
        return _poisoned_result()

    monkeypatch.setattr(ProviderCoreService, "quotes", fake_quotes)
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(
        manager=Manager(),
        health=health,
        query_cache=cache,
        default_provider="tencent",
    )
    try:
        with pytest.raises(IntegrityViolation) as caught:
            service.quotes(["sh600519"], max_age=30.0, with_meta=True)

        assert caught.value.context["phase"] == "provider_result_contract"
        assert caught.value.context["provider"] == "tencent"
        assert caught.value.context["actual_provider"] == "sina"
        assert len(cache) == 0
        assert cache.writes == 0

        state = health.snapshot("tencent", "quote", "quotes")
        assert state.successes == 0
        assert state.failures == 1
        assert state.consecutive_failures == 1
        assert state.circuit_open is True
    finally:
        service.close()
