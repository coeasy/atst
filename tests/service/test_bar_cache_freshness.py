from __future__ import annotations

import time

import pytest

from tstdx.domain.models import Bar
from tstdx.errors import FreshnessViolation
from tstdx.freshness import FreshnessMode, FreshnessStatus
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.service import FreshnessEvidence, QueryResult, ResultMeta


def _plan(*, start: int):  # noqa: ANN202
    return QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols=("sh600519",),
            provider="tdx",
            period="day",
            count=20,
            start=start,
            max_age=300.0,
        )
    )


def _result(*, mode: FreshnessMode, timestamp: str) -> QueryResult[list[Bar]]:
    observed = time.time_ns()
    status = FreshnessStatus(
        verified=True,
        mode=mode,
        basis=(
            "direct_historical_closed_series"
            if mode is FreshnessMode.HISTORICAL_CLOSED
            else "direct_current_series+provider_tail_timestamp"
        ),
        provider_timestamp=timestamp,
        observed_age_seconds=0.0,
        currentness_verified=mode is FreshnessMode.CURRENT_SERIES,
    )
    return QueryResult(
        data=[Bar(datetime=timestamp, close=10.0)],
        meta=ResultMeta(
            provider="tdx",
            channel="quotation",
            capability="bars",
            observed_at_ns=observed,
            freshness=FreshnessEvidence(
                origin="direct",
                observed_at_ns=observed,
                provider_timestamp=timestamp,
            ),
            freshness_status=status,
            real=True,
            fallback=False,
        ),
    )


def test_historical_bar_cache_hit_preserves_historical_mode() -> None:
    plan = _plan(start=20)
    original = _result(
        mode=FreshnessMode.HISTORICAL_CLOSED,
        timestamp="2000-01-03 15:00:00",
    )

    hit = UnifiedMarketDataService._as_cache_hit(plan, original, max_age=300.0)

    assert hit.meta.freshness.origin == "cache"
    assert hit.meta.freshness.cache_hit is True
    assert hit.meta.freshness_status is not None
    assert hit.meta.freshness_status.mode is FreshnessMode.HISTORICAL_CLOSED
    assert hit.meta.freshness_status.currentness_verified is False
    assert (
        hit.meta.freshness_status.basis
        == "bounded_cache:direct_historical_closed_series"
    )
    assert hit.meta.verified_fresh is False


def test_current_bar_cache_hit_rechecks_provider_tail_currentness() -> None:
    plan = _plan(start=0)
    poisoned = _result(
        mode=FreshnessMode.CURRENT_SERIES,
        timestamp="2000-01-03 15:00:00",
    )

    with pytest.raises(FreshnessViolation) as caught:
        UnifiedMarketDataService._as_cache_hit(plan, poisoned, max_age=300.0)

    assert caught.value.context["reason"] == "provider_timestamp_too_old"
