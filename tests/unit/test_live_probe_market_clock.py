# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G4 探针的时钟判据（离线）。

探针里"此刻应该看到哪一天的日线""现在算不算盘中"这两句话是它唯一会误报成红的地带：
写错一次，节假日或开盘前的例行运行就红，而红过一次的探针很快会被无视。所以这两条
判据单独钉在这里，用造出来的时刻跑，不联网。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tests.live.test_tdx_core_chain import expected_daily_bar_date, in_trading_session
from tstdx.domain.calendar import CALENDAR_2026, get_calendar, is_trading_day


def _at(day: date, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day.isoformat()}T{hhmm}:00+08:00")


def test_a_weekend_and_a_holiday_fall_back_to_the_previous_trading_day() -> None:
    calendar = get_calendar()
    # 元旦连休：1/1 与 1/2 都休市（内置表），所以 1/3 周六能看到的最新一根是节前那个交易日
    assert "2026-01-02" in CALENDAR_2026
    saturday = date(2026, 1, 3)
    assert not is_trading_day(saturday)
    assert expected_daily_bar_date(_at(saturday, "10:30")) == calendar.prev_trading_day(saturday)
    assert expected_daily_bar_date(_at(saturday, "10:30")) == date(2025, 12, 31)
    # 国庆休市日：15:30 也必须回退到节前那一个交易日
    holiday = date.fromisoformat(CALENDAR_2026[-1])
    assert expected_daily_bar_date(_at(holiday, "15:30")) == calendar.prev_trading_day(holiday)


@pytest.mark.parametrize("clock", ["09:00", "09:29", "00:01"])
def test_a_trading_day_before_the_open_still_shows_yesterday_bar(clock: str) -> None:
    trading_day = date(2026, 1, 5)  # 周一，非节假日
    assert is_trading_day(trading_day)
    assert expected_daily_bar_date(_at(trading_day, clock)) == get_calendar().prev_trading_day(
        trading_day
    )


def test_from_the_open_onward_the_current_day_is_the_expected_one() -> None:
    """开盘后（含盘中与收盘后到午夜）最新一根就是当天；过午夜归到"开盘前"一侧。"""
    trading_day = date(2026, 1, 5)
    assert expected_daily_bar_date(_at(trading_day, "09:30")) == trading_day
    assert expected_daily_bar_date(_at(trading_day, "15:00")) == trading_day
    assert expected_daily_bar_date(_at(trading_day, "23:59")) == trading_day


def test_the_session_gate_ignores_non_trading_days() -> None:
    trading_day = date(2026, 1, 5)
    assert in_trading_session(_at(trading_day, "10:00"))
    assert in_trading_session(_at(trading_day, "14:00"))
    assert not in_trading_session(_at(trading_day, "12:30"))  # 午休
    assert not in_trading_session(_at(trading_day, "09:20"))  # 集合竞价不算连续竞价
    assert not in_trading_session(_at(date(2026, 1, 3), "10:00"))  # 周六


def test_the_clock_gate_is_measured_on_the_real_now_and_does_not_raise() -> None:
    """探针在 CI 里跑的第一秒就会调这两个函数——它们不许对"现在"抛异常。"""
    from tests.live.test_tdx_core_chain import market_now

    now = market_now()
    assert now.utcoffset().total_seconds() == 8 * 3600
    assert expected_daily_bar_date(now) <= now.date()
    assert isinstance(in_trading_session(now), bool)
