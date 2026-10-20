"""TradingCalendar 覆盖校验 / 线程锁 / 估算标记测试（审计 §2-7）。

覆盖：
* 未覆盖年份 → 懒加载内置表；内置表也没有 → 工作日判断 + 一次性告警；
* set_holidays 覆盖后 is_estimated 对该年改 False；
* 模块级单例线程锁下的并发读写不撕裂。
"""

from __future__ import annotations

import threading
import warnings
from datetime import date

import pytest

from atst.domain.calendar import (
    BUILTIN_CALENDARS,
    TradingCalendar,
    day_like_datetime,
    get_calendar,
    is_trading_day,
)

pytestmark = pytest.mark.unit


class TestDayLikeDatetime:
    """日线及以上 ``Bar.datetime`` 的跨源形状口径（「日期 15:00」）。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-08-28", "2026-08-28 15:00"),
            ("20260828", "2026-08-28 15:00"),
            (" 2026-08-28 ", "2026-08-28 15:00"),
            # 已带时刻（分钟档）原样放过
            ("2026-08-28 09:35", "2026-08-28 09:35"),
            ("2026-08-28 15:00", "2026-08-28 15:00"),
            # 未知形状/空值不臆造
            ("", ""),
            (None, ""),
            ("not-a-date", "not-a-date"),
        ],
    )
    def test_shape_normalisation(self, raw: object, expected: str) -> None:
        assert day_like_datetime(raw) == expected

    def test_market_close_is_not_inferred(self) -> None:
        """港股收盘 16:00，但这里只统一形状：日线一律指向 15:00。"""

        assert day_like_datetime("2026-08-28") == "2026-08-28 15:00"


class TestUncoveredYears:
    """越界年份的覆盖校验。"""

    def test_beyond_builtin_warns_and_falls_back_to_workday(self) -> None:
        cal = TradingCalendar()
        far_year = max(BUILTIN_CALENDARS) + 5
        d = date(far_year, 5, 1)  # 任选一个工作日
        if d.weekday() >= 5:  # 保险：挑下一个周一
            d = date.fromordinal(d.toordinal() + (7 - d.weekday()))
        with pytest.warns(UserWarning, match="按「无节假日」处理"):
            assert cal.is_trading_day(d) is True

    def test_warning_is_once_per_year_per_instance(self) -> None:
        cal = TradingCalendar()
        far_year = max(BUILTIN_CALENDARS) + 5
        d = date(far_year, 6, 1)
        if d.weekday() >= 5:
            d = date.fromordinal(d.toordinal() + (7 - d.weekday()))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cal.is_trading_day(d)
            cal.is_trading_day(d)
            cal.is_trading_day(d)
        assert len([w for w in caught if "无节假日" in str(w.message)]) == 1

    def test_weekend_still_closed_for_uncovered_year(self) -> None:
        """周末在未覆盖年份直接判休（不依赖节假日表，无需告警）。"""
        cal = TradingCalendar()
        far_year = max(BUILTIN_CALENDARS) + 5
        d = date(far_year, 6, 1)
        while d.weekday() < 5:  # 找一个周六
            d = date.fromordinal(d.toordinal() + 1)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # 周末不该触发任何告警
            assert cal.is_trading_day(d) is False

    def test_restricted_calendar_lazy_loads_builtin_year(self) -> None:
        """years=[2024] 的受限日历查询 2026 → 懒加载内置 2026 表而非报错。"""
        cal = TradingCalendar(years=[2024])
        assert cal.is_trading_day("2026-10-01") is False  # 国庆休市（内置 2026 表）

    def test_default_singleton_exposes_new_behavior(self) -> None:
        get_calendar()  # 单例可取
        assert is_trading_day("2025-10-01") is False


class TestSetHolidaysOverridesEstimation:
    """set_holidays 覆盖后 is_estimated 对该年改 False。"""

    def test_estimated_flag_before_and_after_override(self) -> None:
        cal = TradingCalendar()
        assert cal.is_estimated("2026-05-01") is True
        cal.set_holidays(2026, ["2026-05-01"])
        assert cal.is_estimated("2026-05-01") is False
        assert cal.is_trading_day("2026-05-01") is False
        # 其他估算年份不受影响（这里只有 2026；校验 warnings_for 清空）
        assert cal.warnings_for("2026-05-01") == []

    def test_non_estimated_year_unchanged(self) -> None:
        cal = TradingCalendar()
        assert cal.is_estimated("2024-05-01") is False
        cal.set_holidays(2024, ["2024-05-01"])
        assert cal.is_estimated("2024-05-01") is False


class TestThreadLock:
    """模块级单例线程锁下的并发读写。"""

    def test_concurrent_mutation_and_query(self) -> None:
        cal = TradingCalendar()
        errors: list[BaseException] = []

        def mutate() -> None:
            try:
                for i in range(50):
                    cal.set_holidays(2026, [f"2026-07-{i % 28 + 1:02d}"])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def query() -> None:
            try:
                for _ in range(50):
                    assert cal.is_trading_day("2026-07-15") in (True, False)
                    assert cal.is_estimated("2026-07-15") in (True, False)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=mutate) for _ in range(2)]
        threads += [threading.Thread(target=query) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
