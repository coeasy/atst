# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``trading_calendar`` 交易日历在线校准（W9/F1）。

三条边界：

1. 无网/上游失败时回落手动或内置数据，结果带 ``freshness`` 标记并发射
   ``calendar_online_fallback`` 告警——不静默降级；
2. ``set_holidays()`` 手动覆盖优先于在线结果，覆盖过的年份连请求都不发；
3. 在线成功时把交易日反推成休市日写回日历，``estimated`` 变 ``False``。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from atst.diagnostics import WarningCode
from atst.domain.calendar import TradingCalendar
from atst.errors import WebSourceError
from atst.sources._session_research import ResearchSessionMixin


@pytest.fixture
def calendar(monkeypatch: pytest.MonkeyPatch) -> TradingCalendar:
    """每个用例一份独立日历，避免污染进程级单例。"""

    instance = TradingCalendar()
    monkeypatch.setattr("atst.domain.calendar.get_calendar", lambda: instance)
    return instance


def _trading_rows(year: int, *, exclude: set[str] = frozenset()) -> list[dict[str, str]]:
    """构造某年"除 ``exclude`` 外所有工作日都交易"的东财报表行。"""

    days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    return [
        {"TRADE_DATE": f"{day.isoformat()} 00:00:00"}
        for day in (date(year, 1, 1) + timedelta(days=i) for i in range(days))
        if day.weekday() < 5 and day.isoformat() not in exclude
    ]


def test_online_failure_falls_back_with_warning(
    calendar: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args, **kwargs):
        raise WebSourceError("calendar endpoint down")

    monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", boom)
    with pytest.warns(UserWarning, match=WarningCode.CALENDAR_ONLINE_FALLBACK.value):
        rows = ResearchSessionMixin.trading_calendar(year=2026, refresh=True)

    row = rows[0]
    assert row["year"] == 2026
    assert row["fallback"] is True
    assert row["freshness"] == calendar.freshness(2026)
    assert row["source"] == row["freshness"]
    # 内置估算年份回落之后依旧标明"估算"，不冒充权威数据
    assert row["estimated"] is True
    assert row["trading_day_count"] > 0


def test_manual_override_beats_online_and_never_fetches(
    calendar: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    calendar.set_holidays(2026, ["2026-01-01"])

    def must_not_run(*args, **kwargs):
        raise AssertionError("手动覆盖的年份不应再联网校准")

    monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", must_not_run)
    row = ResearchSessionMixin.trading_calendar(year=2026, refresh=True)[0]

    assert row["freshness"] == "manual"
    assert row["source"] == "manual"
    assert row["estimated"] is False
    assert row["fallback"] is False


def test_online_success_calibrates_holidays_and_clears_estimated(
    calendar: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "atst.sources._session_research._eastmoney_rows",
        lambda *args, **kwargs: _trading_rows(2026, exclude={"2026-01-01"}),
    )
    row = ResearchSessionMixin.trading_calendar(year=2026, refresh=True)[0]

    assert row["freshness"] == "online"
    assert row["source"] == "eastmoney"
    assert row["estimated"] is False
    assert row["fallback"] is False
    assert row["holidays"] == ["2026-01-01"]
    # 写回后日历本身也不再是估算值（warnings_for 随之清空）
    assert calendar.is_estimated("2026-01-15") is False
    assert calendar.warnings_for("2026-01-15") == []


def test_second_call_reuses_online_result_without_refetching(
    calendar: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}

    def rows(*args, **kwargs):
        calls["count"] += 1
        return _trading_rows(2026, exclude={"2026-01-01"})

    monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", rows)
    ResearchSessionMixin.trading_calendar(year=2026)
    second = ResearchSessionMixin.trading_calendar(year=2026)

    assert calls["count"] == 1
    assert second[0]["freshness"] == "online"


def test_partial_year_from_upstream_falls_back_instead_of_inventing_holidays(
    calendar: TradingCalendar, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游只回半年（换报表/换市场编码的典型症状）时必须回落，不能把另一半算成休市。"""
    partial = _trading_rows(2026)[:120]
    monkeypatch.setattr(
        "atst.sources._session_research._eastmoney_rows",
        lambda *args, **kwargs: partial,
    )
    with pytest.warns(UserWarning, match=WarningCode.CALENDAR_ONLINE_FALLBACK.value):
        row = ResearchSessionMixin.trading_calendar(year=2026, refresh=True)[0]

    assert row["fallback"] is True
    assert row["freshness"] != "online"
    assert calendar.freshness(2026) != "online"


@pytest.mark.network
def test_online_calibration_against_the_real_endpoint() -> None:
    rows = ResearchSessionMixin.trading_calendar(year=2026, refresh=True)
    assert rows[0]["year"] == 2026
    assert rows[0]["freshness"] in {"online", "estimated"}
