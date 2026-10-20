# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""出口值域尺子的日期维度：``date_problem`` / ``row_violations`` 的日期形状。

``tests/unit/test_domain_integrity.py`` 钉住了 market / code 两把尺子；本文件只管
**日期**——这是 2026-10-13 指数 K 线回归暴露的缺口。

尺子的方向是**拿库自己声明的域反过来量库自己解出来的值**。缺口本身在判据的锚点上：
原来锚定 ``^\\d{4}-\\d{2}-\\d{2}``，只认 4 位年份开头。而记录错位读出的年份位数是
不确定的——实测 ``sh000001`` 指数 K 线错位时产出 ``10112-28-10`` / ``12930-34-20`` /
``103951-51-43``（5~6 位），只有 ``1114-12-45`` 那一种恰好 4 位。于是同一批错位数据
**有时响有时不响**（``sh000300`` 响、``sh000001`` 不响），比没有尺子更坏：调用方会
以为"没告警就是没越域"。

本文件钉住两件事：错位读出的日期形状**全部**落网，以及合法形状**一条不误伤**
（误伤会让 ``field_out_of_domain`` 变成噪声，最终被整体忽略）。
"""

from __future__ import annotations

import pytest

from atst.domain.integrity import date_problem, row_violations

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 合法形状：一条都不许误伤
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-13",
        "1990-12-19",
        "2024-02-29",  # 闰年：真实日历日
        "2026-10-13 15:00",
        "2026-10-13 09:30:00",
        "2026-10-13T15:00",
        "2026-10-13T15:00:59",
        "2026-01-01 00:00",
    ],
)
def test_legal_date_shapes_pass(value: str) -> None:
    assert date_problem(value) is None


def test_leap_day_is_checked_against_the_real_calendar() -> None:
    """2026 不是闰年：``2026-02-29`` 必须落网，``2024-02-29`` 必须放行。"""
    assert date_problem("2026-02-29") is not None
    assert date_problem("2024-02-29") is None


@pytest.mark.parametrize(
    "value",
    [
        "600519",  # 纯代码
        "rb2010",  # 商品代码（字母开头）
        "00700",  # 港股 5 位
        "",  # 空串
        "sh000001",  # canonical symbol
        "2026/10/13",  # 斜杠分隔：不在日期字符集里
        "2026.10.13",
        "12-3-4",  # 年只有 2 位：不够 DATERUN 的 3 位下限
        "1-2-3",
        " 2026-10-13",  # 前导空格：不在日期字符集里
    ],
)
def test_non_date_strings_are_left_alone(value: str) -> None:
    """不像日期的串一律返回 ``None``——尺子只量日期，不量别的字段。"""
    assert date_problem(value) is None


def test_trailing_space_is_a_defect_not_a_non_date() -> None:
    """尾随空格落在日期字符集内（那里本来就容 ``HH:MM`` 前的分隔），形状匹配不上。

    这是**有意**的：日期带尾随 padding 说明产出方有问题，报出来比默默放过好。
    """
    assert date_problem("2026-10-13 ") is not None


# --------------------------------------------------------------------------- #
# 错位读出的日期：全部落网
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        # 真机实测：sh000001 / sh000300 / sz399001 指数 K 线不开指数位时的产出
        "10112-28-10",
        "12930-34-20",
        "103951-51-43",
        "428119-93-66",
        "10479-27-54",
        "5616-57-83",
        "1114-12-45",  # 年份恰好 4 位、月日越界——旧尺子唯一能抓到的那一种
        "8414-91-57",  # 主站垃圾日期
    ],
)
def test_misaligned_dates_are_always_caught(value: str) -> None:
    """错位日期没有"年份位数凑巧"豁免——这是本次回归的核心判据。"""
    assert date_problem(value) is not None


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("2026-13-01", "不是真日历日"),  # 月份越界
        ("2026-00-10", "不是真日历日"),  # 月份为 0
        ("2026-10-32", "不是真日历日"),  # 日期越界
        ("2026-10-00", "不是真日历日"),  # 日期为 0
        ("2026-10-08 25:00", "时分越界"),  # 小时越界
        ("2026-10-08 15:75", "时分越界"),  # 分钟越界
    ],
)
def test_calendar_and_clock_bounds_are_enforced(value: str, fragment: str) -> None:
    problem = date_problem(value)
    assert problem is not None
    assert fragment in problem


def test_dateish_but_wrong_shape_is_rejected() -> None:
    """``10112-2-3`` 不是 ``YYYY-MM-DD``：形状就不对，理由要说清。"""
    problem = date_problem("10112-2-3")
    assert problem is not None
    assert "YYYY-MM-DD" in problem


# --------------------------------------------------------------------------- #
# row_violations：把尺子接回出口
# --------------------------------------------------------------------------- #


def test_row_violations_reports_the_path_of_a_misaligned_date() -> None:
    row = {"date": "10112-28-10", "close": 3797.0}
    problems = row_violations(row)
    assert len(problems) == 1
    assert problems[0].startswith("row.date: ")
    assert "10112-28-10" in problems[0]


def test_row_violations_walks_nested_structures() -> None:
    payload = {"bars": [{"date": "2026-10-13"}, {"date": "12930-34-20"}]}
    problems = row_violations(payload)
    assert len(problems) == 1
    assert problems[0].startswith("row.bars[1].date: ")


def test_row_violations_ignores_clean_rows() -> None:
    payload = {"bars": [{"date": "2026-10-13", "close": 3.5, "volume": 100}]}
    assert row_violations(payload) == []


def test_row_violations_does_not_flag_non_string_dates() -> None:
    """int/float/None 不走日期尺子——数值型的日期字段别被误判成"日期形状"。"""
    assert row_violations({"date": 20261013, "count": None}) == []
