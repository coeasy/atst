"""市场宽度 / 涨停梯队聚合器纯函数测试（S1 + S2）。

聚合逻辑为零网络依赖的纯函数，用罐头数据验证计数、比率、梯队分布与边界。
"""

from __future__ import annotations

import math

import pytest

from tstdx.web.market_stats import (
    MarketBreadth,
    aggregate_breadth,
    aggregate_limit_pool,
)

pytestmark = pytest.mark.unit

# -- 罐头数据 ---------------------------------------------------------------- #
ZT_ROWS = [
    {"code": "600519", "name": "贵州茅台", "limit_up_days": 1, "total": 50},
    {"code": "000001", "name": "平安银行", "limit_up_days": 2, "total": 50},
    {"code": "300750", "name": "宁德时代", "limit_up_days": 2, "total": 50},
    {"code": "601318", "name": "中国平安", "limit_up_days": 3, "total": 50},
]
DT_ROWS = [{"code": "123456", "name": "跌停股", "limit_up_days": 1, "total": 10}]
ZB_ROWS = [{"code": "654321", "name": "炸板股", "limit_up_days": 1, "total": 20}]

BREADTH_ROWS = [
    {"change_pct": 3.5},
    {"change_pct": 1.2},
    {"change_pct": 0.0},
    {"change_pct": -2.1},
    {"change_pct": -0.5},
    {"change_pct": "1.1"},  # 字符串涨跌幅
    {"change_pct": "-3.0"},  # 字符串跌跌幅
    {"change_pct": None},  # 缺值按平盘
]


def test_aggregate_limit_pool_counts_and_broken_rate() -> None:
    lad = aggregate_limit_pool(
        ZT_ROWS,
        DT_ROWS,
        ZB_ROWS,
        zt_total=50,
        dt_total=10,
        zb_total=20,
    )
    assert lad.limit_up_count == 50
    assert lad.limit_down_count == 10
    assert lad.broken_count == 20
    # 炸板率 = 炸板 / (涨停 + 炸板) = 20 / 70
    assert math.isclose(lad.broken_rate, 20 / 70, rel_tol=1e-9)


def test_aggregate_limit_pool_ladder_distribution() -> None:
    lad = aggregate_limit_pool(ZT_ROWS, DT_ROWS, ZB_ROWS, zt_total=50)
    # 连板数分布：1板×1、2板×2、3板×1
    assert lad.board_distribution == {1: 1, 2: 2, 3: 1}
    assert lad.highest_board == 3
    assert lad.first_board_count == 1
    assert lad.consecutive_board_count == 3


def test_aggregate_limit_pool_total_overrides_len() -> None:
    """当传入 zt_total 时，家数用 total 而非 len(rows)。"""
    lad = aggregate_limit_pool(ZT_ROWS, zt_total=120)
    assert lad.limit_up_count == 120  # != len(ZT_ROWS) == 4
    assert lad.board_distribution == {1: 1, 2: 2, 3: 1}  # 分布仍按实拉行


def test_aggregate_limit_pool_empty() -> None:
    lad = aggregate_limit_pool([], [], [])
    assert lad.limit_up_count == 0
    assert lad.broken_rate == 0.0
    assert lad.highest_board == 0
    assert lad.board_distribution == {}


def test_aggregate_limit_pool_broken_rate_zero_when_no_pool() -> None:
    lad = aggregate_limit_pool(ZT_ROWS, dt_total=0, zb_total=0)
    assert lad.broken_count == 0
    assert lad.broken_rate == 0.0


def test_aggregate_breadth_counts() -> None:
    mb = aggregate_breadth(BREADTH_ROWS, limit_up=50, limit_down=10)
    assert mb.up == 3  # 3.5 / 1.2 / "1.1"
    assert mb.down == 3  # -2.1 / -0.5 / "-3.0"
    assert mb.flat == 2  # 0.0 / None
    assert mb.total == 8
    assert mb.limit_up == 50
    assert mb.limit_down == 10
    # 涨停占比 = 50 / 8
    assert math.isclose(mb.limit_up_ratio, 50 / 8, rel_tol=1e-9)
    # 涨跌比 = 3 / 3 = 1.0
    assert mb.as_dict()["advance_decline_ratio"] == 1.0


def test_aggregate_breadth_all_flat() -> None:
    rows = [{"change_pct": 0.0}, {"change_pct": 0.0}, {"change_pct": 0}]
    mb = aggregate_breadth(rows)
    assert (mb.up, mb.down, mb.flat) == (0, 0, 3)
    assert mb.total == 3
    # 下跌家数为 0 → 涨跌比用 None 表示（避免除零）
    assert mb.as_dict()["advance_decline_ratio"] is None


def test_aggregator_return_types() -> None:
    lad = aggregate_limit_pool(ZT_ROWS, DT_ROWS, ZB_ROWS, zt_total=50)
    mb = aggregate_breadth(BREADTH_ROWS)
    assert isinstance(lad, type(aggregate_limit_pool([], [], [])))
    assert isinstance(mb, MarketBreadth)
    # as_dict 可序列化（无 inf / 无嵌套不可序列化对象）
    d = mb.as_dict()
    assert d["advance_decline_ratio"] == 1.0
    lad_d = lad.as_dict()
    assert set(lad_d) >= {
        "limit_up_count",
        "limit_down_count",
        "broken_count",
        "broken_rate",
        "highest_board",
        "board_distribution",
        "first_board_count",
        "consecutive_board_count",
    }
