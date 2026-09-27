# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""A 股统计衍生指标本地聚合器（S1 市场宽度 + S2 涨停梯队 / 炸板率）。

所有函数均为**纯本地聚合**：输入是已解析的字典行（复用东财 clist /
push2ex 涨跌停池），本模块不发起任何网络请求，便于离线罐头测试与复用。

设计要点：
- 量 / 额口径已在解析层统一（股 / 元）；本模块只做计数与比率，不涉及单位换算。
- 涨停 / 跌停 / 炸板家数优先采用池 ``total``（翻页时比 ``len(rows)`` 准），
  无 ``total`` 时退回 ``len(rows)``。
- 连板数取自涨停池行的 ``limit_up_days``（解析层由 ``lbc`` 字段映射），
  首板记为 1、二板记为 2，以此类推。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


def _as_float(value: Any) -> float:
    """将字符串 / 数字 / None 安全地转为 float（空 / None → 0.0）。"""
    if value is None or value == "":
        return 0.0
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    return int(_as_float(value))


@dataclass(frozen=True)
class MarketBreadth:
    """全市场涨跌宽度。

    ``up`` / ``down`` / ``flat`` 由 clist 行的 ``change_pct`` 本地计数得出；
    ``limit_up`` / ``limit_down`` / ``broken`` 由涨跌停池补充（家数）。
    """

    up: int
    down: int
    flat: int
    total: int
    limit_up: int = 0
    limit_down: int = 0
    broken: int = 0
    limit_up_ratio: float = 0.0
    limit_down_ratio: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "up": self.up,
            "down": self.down,
            "flat": self.flat,
            "total": self.total,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "broken": self.broken,
            "limit_up_ratio": self.limit_up_ratio,
            "limit_down_ratio": self.limit_down_ratio,
            # 涨跌比：下跌家数为 0 时用 None 表示（无意义 / 除零）
            "advance_decline_ratio": (self.up / self.down) if self.down else None,
        }


@dataclass(frozen=True)
class LimitUpLadder:
    """涨停梯队 / 连板高度 / 炸板率聚合结果。"""

    limit_up_count: int
    limit_down_count: int
    broken_count: int
    broken_rate: float
    highest_board: int
    board_distribution: dict[int, int]
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def first_board_count(self) -> int:
        """首板（连板数 = 1）家数。"""
        return self.board_distribution.get(1, 0)

    @property
    def consecutive_board_count(self) -> int:
        """连板（≥2 板）家数。"""
        return sum(v for k, v in self.board_distribution.items() if k >= 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit_up_count": self.limit_up_count,
            "limit_down_count": self.limit_down_count,
            "broken_count": self.broken_count,
            "broken_rate": self.broken_rate,
            "highest_board": self.highest_board,
            "board_distribution": self.board_distribution,
            "first_board_count": self.first_board_count,
            "consecutive_board_count": self.consecutive_board_count,
        }


def aggregate_breadth(
    rows: Iterable[Mapping[str, Any]],
    *,
    limit_up: int = 0,
    limit_down: int = 0,
    broken: int = 0,
) -> MarketBreadth:
    """基于 clist 行（含 ``change_pct``）统计全市场涨跌家数。

    Parameters
    ----------
    rows:
        东财 clist 已解析行（``EastmoneyRankSource.fetch_rows`` 输出，含
        ``change_pct``）。
    limit_up / limit_down / broken:
        由涨跌停池提供的涨停 / 跌停 / 炸板家数（可选，单独补充）。
    """
    up = down = flat = 0
    for r in rows:
        pct = _as_float(r.get("change_pct"))
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
    total = up + down + flat
    lu_ratio = (limit_up / total) if total else 0.0
    ld_ratio = (limit_down / total) if total else 0.0
    return MarketBreadth(
        up=up,
        down=down,
        flat=flat,
        total=total,
        limit_up=limit_up,
        limit_down=limit_down,
        broken=broken,
        limit_up_ratio=lu_ratio,
        limit_down_ratio=ld_ratio,
    )


def aggregate_limit_pool(
    zt_rows: Iterable[Mapping[str, Any]],
    dt_rows: Iterable[Mapping[str, Any]] | None = None,
    zb_rows: Iterable[Mapping[str, Any]] | None = None,
    *,
    zt_total: int | None = None,
    dt_total: int | None = None,
    zb_total: int | None = None,
) -> LimitUpLadder:
    """聚合涨停梯队 / 连板高度 / 炸板率。

    Parameters
    ----------
    zt_rows / dt_rows / zb_rows:
        涨停 / 跌停 / 炸板池已解析行（``EastmoneyLimitPoolSource.parse_pool`` 输出）。
    zt_total / dt_total / zb_total:
        池总数（翻页时比 ``len(rows)`` 准确）；缺省用 ``len(rows)``。
    """
    zt = list(zt_rows)
    dt = list(dt_rows or [])
    zb = list(zb_rows or [])

    lu = int(zt_total) if zt_total is not None else len(zt)
    ld = int(dt_total) if dt_total is not None else len(dt)
    bc = int(zb_total) if zb_total is not None else len(zb)
    broken_rate = (bc / (lu + bc)) if (lu + bc) else 0.0

    board_counter: Counter[int] = Counter()
    highest = 0
    for r in zt:
        days = _as_int(r.get("limit_up_days", r.get("lbc", 1)))
        if days < 1:
            days = 1
        board_counter[days] += 1
        if days > highest:
            highest = days

    return LimitUpLadder(
        limit_up_count=lu,
        limit_down_count=ld,
        broken_count=bc,
        broken_rate=broken_rate,
        highest_board=highest,
        board_distribution=dict(sorted(board_counter.items())),
        raw={"zt": zt, "dt": dt, "zb": zb},
    )
