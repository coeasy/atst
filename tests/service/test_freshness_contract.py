from __future__ import annotations

import time

import pytest

from tstdx.errors import FreshnessViolation
from tstdx.freshness import (
    FRESHNESS,
    FreshnessMode,
    FreshnessProfile,
    validate_freshness,
)


def _evidence(**overrides):
    data = {
        "origin": "direct",
        "observed_at_ns": time.time_ns(),
        "provider_timestamp": None,
        "cache_hit": False,
        "replay": False,
        "synthetic": False,
    }
    data.update(overrides)
    return data


def test_tdx_realtime_quote_does_not_invent_provider_wall_clock() -> None:
    profile = FRESHNESS.get("tdx", "quotation", "quotes")
    assert profile.mode is FreshnessMode.DIRECT_SNAPSHOT
    assert profile.require_provider_timestamp is False

    status = validate_freshness(
        _evidence(provider_timestamp=None),
        provider="tdx",
        channel="quotation",
        capability="quotes",
        now_ns=time.time_ns(),
    )
    assert status.verified is True
    assert status.currentness_verified is True
    assert status.basis == "direct_current_snapshot"


def test_replay_cannot_be_live_quote() -> None:
    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            _evidence(origin="replay", replay=True),
            provider="tdx",
            channel="quotation",
            capability="quotes",
        )
    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["reason"] == "not_direct"


def test_cache_hit_cannot_masquerade_as_live_quote() -> None:
    with pytest.raises(FreshnessViolation, match="缓存"):
        validate_freshness(
            _evidence(cache_hit=True),
            provider="tencent",
            channel="quote",
            capability="quotes",
        )


def test_synthetic_cannot_masquerade_as_live_quote() -> None:
    with pytest.raises(FreshnessViolation, match="Synthetic"):
        validate_freshness(
            _evidence(synthetic=True),
            provider="sina",
            channel="quote",
            capability="quotes",
        )


def test_current_series_requires_auditable_tail_timestamp() -> None:
    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            _evidence(provider_timestamp=None),
            provider="tdx",
            channel="quotation",
            capability="bars",
        )
    assert caught.value.context["reason"] == "provider_timestamp_missing"


def test_current_series_accepts_direct_provider_tail_timestamp() -> None:
    status = validate_freshness(
        _evidence(provider_timestamp="2026-09-08 15:00"),
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    assert status.currentness_verified is True
    assert status.basis == "direct_current_series+provider_tail_timestamp"


def test_old_observation_is_rejected_even_when_origin_was_direct() -> None:
    now = time.time_ns()
    evidence = _evidence(observed_at_ns=now - 10_000_000_000)
    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            evidence,
            provider="tdx",
            channel="quotation",
            capability="quotes",
            now_ns=now,
        )
    assert caught.value.context["reason"] == "observation_too_old"


def test_local_historical_never_satisfies_live_requirement() -> None:
    with pytest.raises(FreshnessViolation, match="local_historical"):
        validate_freshness(
            _evidence(
                origin="local",
                provider_timestamp="2026-09-05 15:00",
            ),
            provider="tdx",
            channel="vipdoc",
            capability="bars",
            require_live=True,
        )


def test_business_date_profile_is_not_forced_into_quote_ttl() -> None:
    profile = FreshnessProfile(
        mode=FreshnessMode.BUSINESS_DATE,
        require_provider_timestamp=True,
        max_observation_age_seconds=None,
    )
    status = validate_freshness(
        _evidence(provider_timestamp="2026-09-08"),
        provider="eastmoney",
        channel="corporate",
        capability="corporate",
        profile=profile,
    )
    assert status.basis == "provider_business_date"
