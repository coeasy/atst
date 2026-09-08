from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tstdx.domain.models import Bar
from tstdx.errors import FreshnessViolation
from tstdx.freshness import FreshnessMode, FreshnessProfile, validate_freshness
from tstdx.service import UnifiedMarketDataService


def _now_ns(value: str = "2026-09-08T09:00:00+00:00") -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)


def _evidence(timestamp: str, *, now_ns: int) -> dict[str, object]:
    return {
        "origin": "direct",
        "observed_at_ns": now_ns,
        "provider_timestamp": timestamp,
        "cache_hit": False,
        "replay": False,
        "synthetic": False,
    }


def test_freshness_profile_keeps_historical_positional_description_slot() -> None:
    profile = FreshnessProfile(
        FreshnessMode.CURRENT_SERIES,
        True,
        True,
        False,
        False,
        False,
        30.0,
        "legacy positional description",
    )

    assert profile.description == "legacy positional description"
    assert profile.max_provider_calendar_age_days is None


def test_current_series_rejects_unparseable_provider_tail_timestamp() -> None:
    now = _now_ns()
    profile = FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_provider_calendar_age_days=14,
    )

    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            _evidence("not-a-time", now_ns=now),
            provider="tencent",
            channel="kline",
            capability="bars",
            profile=profile,
            now_ns=now,
        )

    assert caught.value.context["reason"] == "provider_timestamp_unparseable"


def test_current_series_rejects_old_provider_tail_timestamp() -> None:
    now = _now_ns()
    profile = FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_provider_calendar_age_days=14,
    )

    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            _evidence("2026-01-01 15:00:00", now_ns=now),
            provider="tencent",
            channel="kline",
            capability="bars",
            profile=profile,
            now_ns=now,
        )

    assert caught.value.context["reason"] == "provider_timestamp_too_old"
    assert caught.value.context["max_age_days"] == 14


def test_historical_closed_series_is_auditable_but_not_current() -> None:
    now = _now_ns()
    profile = FreshnessProfile(
        mode=FreshnessMode.HISTORICAL_CLOSED,
        require_provider_timestamp=True,
    )

    status = validate_freshness(
        _evidence("2019-01-02 15:00:00", now_ns=now),
        provider="tdx",
        channel="quotation",
        capability="bars",
        profile=profile,
        now_ns=now,
        require_live=False,
    )

    assert status.verified is True
    assert status.currentness_verified is False
    assert status.basis == "direct_historical_closed_series"


def test_historical_closed_series_cannot_satisfy_live_requirement() -> None:
    now = _now_ns()
    profile = FreshnessProfile(
        mode=FreshnessMode.HISTORICAL_CLOSED,
        require_provider_timestamp=True,
    )

    with pytest.raises(FreshnessViolation) as caught:
        validate_freshness(
            _evidence("2019-01-02 15:00:00", now_ns=now),
            provider="tdx",
            channel="quotation",
            capability="bars",
            profile=profile,
            now_ns=now,
            require_live=True,
        )

    assert caught.value.context["reason"] == "historical_closed"


class StaleBarAdapter:
    def fetch_bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        adjust: str,
    ) -> list[Bar]:
        return [
            Bar(
                datetime="2000-01-03 15:00:00",
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.5,
                volume=100,
                amount=1000.0,
            )
        ]


class StaleBarManager:
    def __init__(self) -> None:
        self.adapter = StaleBarAdapter()

    def bar_adapter(self, provider: str, *, period: str = "day"):
        assert provider == "tencent"
        assert period == "day"
        return "kline", self.adapter

    def close(self) -> None:
        pass


def test_provider_service_rejects_stale_current_bar_tail() -> None:
    service = UnifiedMarketDataService(manager=StaleBarManager())

    try:
        with pytest.raises(FreshnessViolation) as caught:
            service.bars(
                "sh600519",
                provider="tencent",
                period="day",
                start=0,
            )
    finally:
        service.close()

    assert caught.value.context["provider"] == "tencent"
    assert caught.value.context["channel"] == "kline"
    assert caught.value.context["capability"] == "bars"
    assert caught.value.context["reason"] == "provider_timestamp_too_old"
