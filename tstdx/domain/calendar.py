# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""A 股交易日历与交易时段（§23.3）。

规则
----
* 周末（周六/周日）休市
* 法定节假日休市（国务院公布的放假安排）
* **周末调休上班日 A 股仍休市**（与银行/柜台不同）
* 交易时段：09:30–11:30、13:00–15:00
* 集合竞价：09:15–09:25（其中 09:20–09:25 不可撤单）；收盘竞价 14:57–15:00

数据来源
--------
内置表覆盖 2024–2026。其中 **2026 年为估算值**（``estimated=True``），
国务院正式公布后需调用 :meth:`TradingCalendar.set_holidays` 更新。
在线校准（:meth:`TradingCalendar.update_from_web`）**尚未实现**，调用即抛
``NotImplementedError``，不要把它当作可用的降级路径。

.. warning::
   ``estimated`` 数据仅用于离线降级，**不得**作为交易决策的唯一依据。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from ..diagnostics import WarningCode, record_warning
from ..errors import CalendarError

__all__ = [
    "TradingSession",
    "TradingCalendar",
    "CALENDAR_2024",
    "CALENDAR_2025",
    "CALENDAR_2026",
    "BUILTIN_CALENDARS",
    "get_calendar",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "trading_days_between",
]


class TradingSession:
    """交易时段常量（分钟，从 0 点起算）。"""

    OPEN_AUCTION = (9 * 60 + 15, 9 * 60 + 25)  # 09:15–09:25 集合竞价
    MORNING = (9 * 60 + 30, 11 * 60 + 30)  # 09:30–11:30
    AFTERNOON = (13 * 60, 15 * 60)  # 13:00–15:00
    CLOSE_AUCTION = (14 * 60 + 57, 15 * 60)  # 14:57–15:00 收盘竞价

    @classmethod
    def all_sessions(cls) -> list[tuple[int, int]]:
        return [cls.MORNING, cls.AFTERNOON]

    @classmethod
    def minutes_per_day(cls) -> int:
        return (cls.MORNING[1] - cls.MORNING[0]) + (cls.AFTERNOON[1] - cls.AFTERNOON[0])

    @classmethod
    def in_session(cls, minutes: int) -> bool:
        return any(a <= minutes < b for a, b in cls.all_sessions())

    @classmethod
    def in_auction(cls, minutes: int) -> bool:
        return cls.OPEN_AUCTION[0] <= minutes < cls.OPEN_AUCTION[1]


#: 2024 年休市日（法定节假日，周末除外）
CALENDAR_2024: tuple[str, ...] = (
    # 元旦
    "2024-01-01",
    # 春节 2/9-2/17
    "2024-02-09",
    "2024-02-12",
    "2024-02-13",
    "2024-02-14",
    "2024-02-15",
    "2024-02-16",
    # 清明
    "2024-04-04",
    "2024-04-05",
    # 劳动节 5/1-5/5
    "2024-05-01",
    "2024-05-02",
    "2024-05-03",
    # 端午
    "2024-06-10",
    # 中秋
    "2024-09-16",
    "2024-09-17",
    # 国庆 10/1-10/7
    "2024-10-01",
    "2024-10-02",
    "2024-10-03",
    "2024-10-04",
    "2024-10-07",
)

#: 2025 年休市日
CALENDAR_2025: tuple[str, ...] = (
    # 元旦
    "2025-01-01",
    # 春节 1/28-2/4
    "2025-01-28",
    "2025-01-29",
    "2025-01-30",
    "2025-01-31",
    "2025-02-03",
    "2025-02-04",
    # 清明
    "2025-04-04",
    # 劳动节 5/1-5/5
    "2025-05-01",
    "2025-05-02",
    "2025-05-05",
    # 端午
    "2025-05-31",
    "2025-06-02",
    # 国庆+中秋 10/1-10/8
    "2025-10-01",
    "2025-10-02",
    "2025-10-03",
    "2025-10-06",
    "2025-10-07",
    "2025-10-08",
)

#: 2026 年休市日 —— **估算值**，国务院正式公布后需更新
CALENDAR_2026: tuple[str, ...] = (
    # 元旦 1/1-1/3
    "2026-01-01",
    "2026-01-02",
    # 春节（农历正月初一约 2/17）
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    # 清明
    "2026-04-06",
    # 劳动节
    "2026-05-01",
    "2026-05-04",
    "2026-05-05",
    # 端午
    "2026-06-19",
    # 中秋
    "2026-09-25",
    # 国庆
    "2026-10-01",
    "2026-10-02",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2026-10-08",
)

BUILTIN_CALENDARS: dict[int, tuple[str, ...]] = {
    2024: CALENDAR_2024,
    2025: CALENDAR_2025,
    2026: CALENDAR_2026,
}

#: 哪些年份的内置数据是估算值
ESTIMATED_YEARS: frozenset[int] = frozenset({2026})


class TradingCalendar:
    """A 股交易日历。

    线程模型：所有可变操作（``set_holidays`` / ``add_holiday`` /
    ``mark_workday``）与触发懒加载的查询（``is_trading_day``）经由模块级
    :data:`_CALENDAR_LOCK` 串行化——默认单例进程内共享，未加锁年代与节假日
    集合并发读写存在撕裂风险（审计 §2-7）。
    """

    def __init__(self, years: Iterable[int] | None = None) -> None:
        self._holidays: set[date] = set()
        self._loaded_years: set[int] = set()
        for y in years if years is not None else BUILTIN_CALENDARS:
            self._holidays.update(self._year_holidays(y))
            self._loaded_years.add(y)
        self._extra_holidays: set[date] = set()  # 临时休市（如重大事件）
        self._workdays: set[date] = set()  # 周末调休上班（A 股仍休市，仅记录）
        # set_holidays() 覆盖过的年份：这些年份的内置估算数据已失效
        self._overridden_years: set[int] = set()
        # 已就「未覆盖年份」告警过的年份（once 语义，按实例去重）
        self._uncovered_warned: set[int] = set()

    # -- 数据维护 ---------------------------------------------------------- #
    @staticmethod
    def _year_holidays(year: int) -> set[date]:
        raw = BUILTIN_CALENDARS.get(year)
        if raw is None:
            raise CalendarError(
                f"内置日历未覆盖 {year} 年；请用 set_holidays() 补充",
                context={"year": year, "covered": sorted(BUILTIN_CALENDARS)},
            )
        out = set()
        for s in raw:
            y, m, d = (int(x) for x in s.split("-"))
            out.add(date(y, m, d))
        return out

    def _ensure(self, year: int) -> bool:
        """确保 ``year`` 年节假日已加载。

        先查已加载年份，再尝试内置表（懒加载；读现有结构，不重复解析）。
        内置表也没有该年 → 返回 False，由调用方决定降级语义。

        仅供内部使用；调用方需已持有 :data:`_CALENDAR_LOCK`。
        """
        if year in self._loaded_years:
            return True
        try:
            self._holidays.update(self._year_holidays(year))
        except CalendarError:
            return False
        self._loaded_years.add(year)
        return True

    def _warn_uncovered(self, year: int) -> None:
        """对未覆盖年份做一次性告警（once：按实例 + 年份去重）。"""
        fresh = year not in self._uncovered_warned
        self._uncovered_warned.add(year)
        record_warning(
            WarningCode.CALENDAR_YEAR_UNCOVERED,
            f"交易日历未覆盖 {year} 年（内置表仅到 "
            f"{max(BUILTIN_CALENDARS)} 年）：该年节假日按「无节假日」处理，"
            "法定节假日将被误判为交易日。请用 set_holidays() 补充该年数据。",
            stacklevel=3,
            stderr=fresh,
        )

    def set_holidays(self, year: int, days: Iterable[str]) -> None:
        """覆盖某年的休市日（用于校正估算数据）。

        覆盖后该年视为**权威数据**：:meth:`is_estimated` 对该年改回 False，
        :meth:`warnings_for` 不再告警，且该年进入已加载集合（越界懒加载命中）。
        """
        with _CALENDAR_LOCK:
            self._holidays -= {d for d in self._holidays if d.year == year}
            # 同一年的历史 extra 也要清掉，否则二次覆盖会残留旧假日
            self._extra_holidays = {d for d in self._extra_holidays if d.year != year}
            for s in days:
                y, m, d = (int(x) for x in s.split("-"))
                self._extra_holidays.add(date(y, m, d))
            # 重建：把新增的并入
            self._holidays.update(self._extra_holidays)
            self._loaded_years.add(year)
            self._overridden_years.add(year)

    def add_holiday(self, day: str) -> None:
        with _CALENDAR_LOCK:
            y, m, d = (int(x) for x in day.split("-"))
            self._holidays.add(date(y, m, d))
            self._loaded_years.add(y)

    def mark_workday(self, day: str) -> None:
        """记录周末调休上班日（不改变 A 股休市判定，仅供展示）。"""
        with _CALENDAR_LOCK:
            y, m, d = (int(x) for x in day.split("-"))
            self._workdays.add(date(y, m, d))

    # -- 查询 -------------------------------------------------------------- #
    def is_trading_day(self, d: date | str) -> bool:
        d = _to_date(d)
        if d.weekday() >= 5:  # 周六/周日
            return False
        with _CALENDAR_LOCK:
            # 覆盖校验：越界年份先尝试内置表懒加载；仍无法覆盖 →
            # 按无节假日处理（工作日视为交易日）并一次性告警（审计 §2-7）
            if d.year not in self._loaded_years and not self._ensure(d.year):
                self._warn_uncovered(d.year)
                return True
            return d not in self._holidays

    def next_trading_day(self, d: date | str, *, inclusive: bool = False) -> date:
        d = _to_date(d)
        if not inclusive:
            d += timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def prev_trading_day(self, d: date | str, *, inclusive: bool = False) -> date:
        d = _to_date(d)
        if not inclusive:
            d -= timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def trading_days_between(self, start: date | str, end: date | str) -> list[date]:
        s, e = _to_date(start), _to_date(end)
        if s > e:
            raise CalendarError(
                f"起始日期晚于结束日期: {s} > {e}", context={"start": str(s), "end": str(e)}
            )
        out: list[date] = []
        cur = s
        while cur <= e:
            if self.is_trading_day(cur):
                out.append(cur)
            cur += timedelta(days=1)
        return out

    def count_trading_days(self, start: date | str, end: date | str) -> int:
        return len(self.trading_days_between(start, end))

    def is_estimated(self, d: date | str) -> bool:
        """该日期所在年份的日历是否为估算值。

        :meth:`set_holidays` 覆盖过的年份视为权威数据 → False。
        """
        year = _to_date(d).year
        with _CALENDAR_LOCK:
            return year in ESTIMATED_YEARS and year not in self._overridden_years

    def warnings_for(self, d: date | str) -> list[str]:
        if self.is_estimated(d):
            return [
                f"{_to_date(d).year} 年节假日为估算值，"
                f"请用 set_holidays() 或 update_from_web() 校准"
            ]
        return []

    def update_from_web(self, *args: Any, **kwargs: Any) -> None:
        """从公开日历源校准（需配合 :mod:`tstdx.web` 的交易日历适配器）。"""
        raise NotImplementedError(
            "交易日历在线校准在 W14b HTTP Web 源批次实现；当前请用 set_holidays() 手动补充"
        )


def _to_date(d: date | str) -> date:
    if isinstance(d, date):
        return d
    s = str(d).strip()[:10].replace("/", "-")
    parts = s.split("-")
    if len(parts) != 3:
        raise CalendarError(f"无法解析日期: {d!r}")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError) as exc:
        raise CalendarError(
            f"无法解析日期: {d!r}（应为 YYYY-MM-DD）", context={"raw": str(d)}
        ) from exc


#: 模块级单例线程锁：保护 ``_default_calendar``（及所有 TradingCalendar 实例）
#: 的节假日集合读写与懒加载。审计 §2-7：单例跨线程共享，日历端须自证并发
#: 安全并保留未覆盖年份告警。
_CALENDAR_LOCK = threading.RLock()

_default_calendar = TradingCalendar()


def get_calendar() -> TradingCalendar:
    return _default_calendar


def is_trading_day(d: date | str) -> bool:
    return _default_calendar.is_trading_day(d)


def next_trading_day(d: date | str, *, inclusive: bool = False) -> date:
    return _default_calendar.next_trading_day(d, inclusive=inclusive)


def prev_trading_day(d: date | str, *, inclusive: bool = False) -> date:
    return _default_calendar.prev_trading_day(d, inclusive=inclusive)


def trading_days_between(start: date | str, end: date | str) -> list[date]:
    return _default_calendar.trading_days_between(start, end)
