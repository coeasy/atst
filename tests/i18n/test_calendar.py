"""A 股交易日历测试（§23.3）：验证 2024-2026 节假日、周末、交易时段。

覆盖：已知节假日、周末非交易日、is_trading_day/is_trading_time、
下一交易日/前一交易日、估算年份标记。
"""

from __future__ import annotations

from datetime import date

import pytest

from atst.domain.calendar import (
    BUILTIN_CALENDARS,
    CALENDAR_2024,
    ESTIMATED_YEARS,
    TradingCalendar,
    TradingSession,
    is_trading_day,
    next_trading_day,
    prev_trading_day,
)
from atst.errors import CalendarError


@pytest.mark.unit
class TestTradingCalendar:
    """交易日历测试。"""

    def test_builtin_calendars_exist(self):
        """#1 内置日历覆盖 2024-2026。"""
        assert 2024 in BUILTIN_CALENDARS
        assert 2025 in BUILTIN_CALENDARS
        assert 2026 in BUILTIN_CALENDARS
        assert len(CALENDAR_2024) >= 10  # 至少有多个节假日

    def test_new_years_day_2024(self):
        """#2 2024-01-01 元旦 → 非交易日。"""
        cal = TradingCalendar()
        assert cal.is_trading_day("2024-01-01") is False
        assert cal.is_trading_day(date(2024, 1, 1)) is False

    def test_spring_festival_2024(self):
        """#3 2024 春节（2024-02-09 至 2024-02-16）→ 非交易日。"""
        cal = TradingCalendar()
        for d in [
            "2024-02-09",
            "2024-02-12",
            "2024-02-13",
            "2024-02-14",
            "2024-02-15",
            "2024-02-16",
        ]:
            assert cal.is_trading_day(d) is False, f"{d} 应为春节休市"

    def test_national_day_2024(self):
        """#4 2024-10-01 国庆 → 非交易日。"""
        cal = TradingCalendar()
        assert cal.is_trading_day("2024-10-01") is False
        assert cal.is_trading_day("2024-10-02") is False

    def test_weekend_non_trading(self):
        """#5 周末（周六/周日）→ 非交易日。"""
        cal = TradingCalendar()
        # 2024-01-06 是周六
        assert cal.is_trading_day(date(2024, 1, 6)) is False
        # 2024-01-07 是周日
        assert cal.is_trading_day(date(2024, 1, 7)) is False

    def test_weekday_trading_day(self):
        """#6 普通工作日 → 交易日。"""
        cal = TradingCalendar()
        # 2024-01-02 是周二，非节假日
        assert cal.is_trading_day(date(2024, 1, 2)) is True

    def test_next_trading_day(self):
        """#7 下一交易日。"""
        cal = TradingCalendar()
        # 从周五 2024-01-05 开始 → 下一个交易日是周一 2024-01-08（不是元旦）
        nd = cal.next_trading_day(date(2024, 1, 5))
        assert nd == date(2024, 1, 8)  # 跳过周末

        # 从除夕前一天（周六 2024-02-10）→ 下一个交易日
        nd2 = cal.next_trading_day(date(2024, 2, 10))
        assert nd2 >= date(2024, 2, 17)  # 跳过春节假期

    def test_prev_trading_day(self):
        """#8 前一交易日。"""
        cal = TradingCalendar()
        # 从周一 2024-01-08 → 前一交易日是周五 2024-01-05
        pd = cal.prev_trading_day(date(2024, 1, 8))
        assert pd == date(2024, 1, 5)

    def test_trading_days_between(self):
        """#9 两个日期之间的交易日列表。"""
        cal = TradingCalendar()
        days = cal.trading_days_between(date(2024, 1, 2), date(2024, 1, 6))
        # 1/2(周二) 1/3(周三) 1/4(周四) 1/5(周五) 是交易日
        # 1/6(周六) 不是
        assert date(2024, 1, 2) in days
        assert date(2024, 1, 5) in days
        assert date(2024, 1, 6) not in days

    def test_is_estimated(self):
        """#10 2026 年是估算值。"""
        cal = TradingCalendar()
        assert cal.is_estimated(date(2026, 1, 1)) is True
        assert cal.is_estimated(date(2024, 1, 1)) is False
        assert 2026 in ESTIMATED_YEARS
        assert 2024 not in ESTIMATED_YEARS

    def test_warnings_for_estimated(self):
        """#11 估算年份返回警告。"""
        cal = TradingCalendar()
        warns = cal.warnings_for(date(2026, 6, 1))
        assert len(warns) > 0
        assert "估算" in warns[0]

    def test_set_holidays_override(self):
        """#12 set_holidays 覆盖某年数据。"""
        cal = TradingCalendar(years=[2024])
        # 覆盖 2024 年
        cal.set_holidays(2024, ["2024-01-01"])
        assert cal.is_trading_day(date(2024, 1, 1)) is False

    def test_add_holiday(self):
        """#13 add_holiday 添加临时休市。"""
        cal = TradingCalendar(years=[2024])
        cal.add_holiday("2024-12-25")
        assert cal.is_trading_day(date(2024, 12, 25)) is False

    def test_uncovered_year_raises(self):
        """#14 未覆盖年份 → CalendarError。"""
        with pytest.raises(CalendarError):
            TradingCalendar(years=[2099])

    def test_global_functions(self):
        """#15 全局函数委托默认日历。"""
        assert is_trading_day("2024-01-01") is False
        assert is_trading_day("2024-01-02") is True
        nd = next_trading_day("2024-01-05")
        assert nd == date(2024, 1, 8)
        pd = prev_trading_day("2024-01-08")
        assert pd == date(2024, 1, 5)

    def test_invalid_date_format(self):
        """#16 无效日期格式 → CalendarError。"""
        cal = TradingCalendar()
        with pytest.raises(CalendarError):
            cal.is_trading_day("not-a-date")
        with pytest.raises(CalendarError):
            cal.is_trading_day("2024/13/01")  # 无效月份


@pytest.mark.unit
class TestTradingSession:
    """交易时段测试。"""

    def test_session_constants(self):
        """#17 交易时段常量。"""
        assert TradingSession.MORNING == (9 * 60 + 30, 11 * 60 + 30)
        assert TradingSession.AFTERNOON == (13 * 60, 15 * 60)

    def test_in_session(self):
        """#18 in_session 判断。"""
        # 10:00 = 600 分钟 → 在上午盘中
        assert TradingSession.in_session(600) is True
        # 08:00 = 480 分钟 → 不在盘中
        assert TradingSession.in_session(480) is False
        # 12:00 = 720 分钟 → 不在盘中（午间休市）
        assert TradingSession.in_session(720) is False
        # 14:00 = 840 分钟 → 在下午盘中
        assert TradingSession.in_session(840) is True

    def test_in_auction(self):
        """#19 in_auction 判断。"""
        # 09:20 = 560 分钟 → 在集合竞价中
        assert TradingSession.in_auction(560) is True
        # 09:30 = 570 分钟 → 不在集合竞价中
        assert TradingSession.in_auction(570) is False

    def test_minutes_per_day(self):
        """#20 每日交易分钟数。"""
        mins = TradingSession.minutes_per_day()
        # 上午 2 小时 + 下午 2 小时 = 240 分钟
        assert mins == 240
