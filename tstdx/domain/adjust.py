# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""复权引擎（§11）：前复权 / 后复权 / 定点复权。

**定位：公共 API，生产接线排期批次 G4**——当前无生产链路消费本模块
（1.1.0 未接入 client/facade 降级链），仅供离线复权计算使用。

除权除息数学
------------
设事件日除权前收盘价为 :math:`P`，每股现金红利 :math:`D`，
每股送转比例 :math:`S`，每股配股比例 :math:`R`，配股价 :math:`P_r`
（协议中 dividend / bonus_ratio / rights_ratio 均为**每 10 股**口径，此处已除 10）。

**除权参考价**

.. math::

    P_{ref} = \\frac{P - D + P_r \\times R}{1 + S + R}

**调整系数**

.. math::

    k = \\frac{P_{ref}}{P}

**复权因子**

* 后复权因子（事件日**及之后**累乘）:  :math:`f^{price}_i = \\prod_{d \\le i} \\frac{1}{k_d}`
* 成交量因子（股本扩张还原）:          :math:`f^{vol}_i = \\prod_{d \\le i} \\frac{1}{1 + S_d + R_d}`
* 后复权:  :math:`price \\times f^{price}`, :math:`volume \\times f^{vol}`
* 前复权:  后复权结果再除以**最后一根 bar** 的因子（归一化到最新口径）
* 定点复权: 后复权结果除以 ``anchor_date`` 当日因子

**缺前收盘价的降级口径**：bar 未携带 ``extra["prev_close"]`` 时，价格因子
退化为 ``1/(1+S+R)``（只还原股本扩张，**忽略现金红利**）——现金红利会
因此丢失，引擎对此做一次性 :class:`warnings.warn` 提示（见
:func:`_price_adjust_ratio`）。需要精确复权请确保事件携带前收盘价。

事件类别过滤
------------
``CapitalChange.category`` 中只有 **1（除权除息）** 参与价格因子计算；
其他类别（增发、回购、股本变化等）不影响除权价，仅记录在案。
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as _date
from typing import Any

from ..errors import AdjustError
from .models import Bar, CapitalChange

__all__ = [
    "AdjustMethod",
    "AdjustFactor",
    "AdjustEngine",
    "to_adjusted",
    "compute_factors",
]

#: 参与复权计算的事件类别（1=除权除息）
ADJUST_CATEGORIES = frozenset({1})


class AdjustMethod:
    NONE = "none"
    QFQ = "qfq"  # 前复权
    HFQ = "hfq"  # 后复权
    FIXED = "fixed"  # 定点复权（需 anchor_date）


@dataclass
class AdjustFactor:
    """单根 bar 的复权因子。"""

    date: str
    price_factor: float
    volume_factor: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "price_factor": round(self.price_factor, 8),
            "volume_factor": round(self.volume_factor, 8),
        }


def _parse_date(s: str) -> _date:
    s = s.strip()[:10]
    parts = s.replace("/", "-").split("-")
    if len(parts) < 3:
        raise AdjustError(f"无法解析日期: {s!r}")
    try:
        return _date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise AdjustError(f"非法日期: {s!r}") from exc


def compute_factors(
    bars: Sequence[Bar],
    events: Iterable[CapitalChange],
    *,
    adjust_categories: frozenset[int] = ADJUST_CATEGORIES,
) -> list[AdjustFactor]:
    """计算每根 bar 的复权因子。

    Parameters
    ----------
    bars:
        按时间**升序**排列的不复权 K 线。
    events:
        除权除息事件（0x000F / 本地 gbbq 文件）。
    """
    if not bars:
        return []

    # 事件按日期升序，且与 bar 对齐（日期解析移出内层循环：
    # 原实现每个 bar 都重复 _parse_date(ev.date)，事件多时 O(n×m) 白算）
    ev_list = sorted(
        (e for e in events if e.category in adjust_categories),
        key=lambda e: _parse_date(e.date),
    )
    ev_dates = [_parse_date(e.date) for e in ev_list]
    ev_idx = 0
    n_ev = len(ev_list)

    price_factor = 1.0
    vol_factor = 1.0
    factors: list[AdjustFactor] = []

    bar_dates = [_parse_date(b.datetime) for b in bars]

    for i, bar in enumerate(bars):
        d = bar_dates[i]
        # 应用所有 date <= 当前 bar 日期的事件
        # （除权日当天开盘价已反映除权，故事件日及之后的 bar 乘以因子）
        while ev_idx < n_ev and ev_dates[ev_idx] <= d:
            ev = ev_list[ev_idx]
            price_factor *= _price_adjust_ratio(ev, bar)
            vol_factor *= _volume_adjust_ratio(ev)
            ev_idx += 1
        factors.append(
            AdjustFactor(date=bar.datetime, price_factor=price_factor, volume_factor=vol_factor)
        )

    return factors


#: 「缺前收盘价忽略现金红利」的一次性告警旗标（once 语义）
_warned_missing_prev_close = False


def _price_adjust_ratio(ev: CapitalChange, bar: Bar) -> float:
    """返回 :math:`1/k`（后复权方向的价格放大系数）。

    缺前收盘价（``extra["prev_close"]``）时退化为 ``1/(1+S+R)``：
    现金红利被忽略，误差 = D/P 量级。首次命中时发一次性
    :class:`UserWarning` 提示（docstring 契约与实际行为此前不一致，
    审计 §3-7）。
    """
    global _warned_missing_prev_close
    d = ev.dividend / 10.0  # 每股现金红利
    s = ev.bonus_ratio / 10.0  # 每股送转
    r = ev.rights_ratio / 10.0  # 每股配股
    pr = ev.rights_price

    denom = 1.0 + s + r
    if denom <= 0:
        raise AdjustError(
            f"非法送配比例: bonus={ev.bonus_ratio}, rights={ev.rights_ratio}",
            context={"code": ev.code, "date": ev.date},
        )

    # 前收盘：优先用 bar 的前收盘记录（extra 可能为 None，统一防御）
    extra = getattr(bar, "extra", None) or {}
    prev_close = extra.get("prev_close")
    prev_close = float(prev_close) if prev_close else None

    if prev_close and prev_close > 0:
        ref_price = (prev_close - d + pr * r) / denom
        if ref_price <= 0:
            raise AdjustError(
                "除权参考价非正，事件数据异常",
                context={"code": ev.code, "date": ev.date, "ref": ref_price},
            )
        k = ref_price / prev_close
        if k <= 0:
            raise AdjustError(f"调整系数非正: k={k}", context={"code": ev.code, "date": ev.date})
        return 1.0 / k

    # 无前收盘价：仅按股本扩张倍数还原，现金红利被忽略 —— 一次性告警
    if d and not _warned_missing_prev_close:
        _warned_missing_prev_close = True
        warnings.warn(
            f"复权事件 {ev.date} 缺少前收盘价（bar.extra['prev_close']），"
            f"每股现金红利 {d:.4f} 元被忽略：价格因子按 1/(1+S+R) 近似。"
            "精确复权请提供前收盘价。",
            stacklevel=3,
        )
    return denom


def _volume_adjust_ratio(ev: CapitalChange) -> float:
    """成交量放大系数（股本扩张还原方向）。

    与 :func:`_price_adjust_ratio` 的分母防御**统一为 raise**：
    ``1+S+R <= 0`` 属于事件数据错误，静默返回 1.0 会把错误因子
    混入下游（审计 §3-7 要求两处口径一致）。
    """
    s = ev.bonus_ratio / 10.0
    r = ev.rights_ratio / 10.0
    denom = 1.0 + s + r
    if denom <= 0:
        raise AdjustError(
            f"非法送配比例（成交量因子分母非正）: bonus={ev.bonus_ratio}, rights={ev.rights_ratio}",
            context={"code": ev.code, "date": ev.date},
        )
    return 1.0 / denom


class AdjustEngine:
    """复权引擎。

    Example
    -------
    >>> engine = AdjustEngine()
    >>> qfq = engine.apply(bars, events, method="qfq")
    >>> fixed = engine.apply(bars, events, method="fixed", anchor_date="2024-06-30")
    """

    def __init__(self, *, adjust_volume: bool = True, round_price: int = 4) -> None:
        self.adjust_volume = adjust_volume
        self.round_price = round_price

    def factors(self, bars: Sequence[Bar], events: Iterable[CapitalChange]):
        return compute_factors(bars, events)

    def apply(
        self,
        bars: Sequence[Bar],
        events: Iterable[CapitalChange],
        method: str = AdjustMethod.QFQ,
        *,
        anchor_date: str | None = None,
    ) -> list[Bar]:
        if method == AdjustMethod.NONE or not events:
            return list(bars)

        factors = compute_factors(bars, events)
        if len(factors) != len(bars):
            raise AdjustError(
                "因子数量与 K 线数量不一致",
                context={"bars": len(bars), "factors": len(factors)},
            )
        if not factors:
            return list(bars)

        if method == AdjustMethod.HFQ:
            base_price, base_vol = 1.0, 1.0
        elif method == AdjustMethod.QFQ:
            base_price = factors[-1].price_factor
            base_vol = factors[-1].volume_factor
        elif method == AdjustMethod.FIXED:
            if anchor_date is None:
                raise AdjustError("定点复权必须指定 anchor_date")
            anchor = _parse_date(anchor_date)
            idx = None
            for i, f in enumerate(factors):
                if _parse_date(f.date) >= anchor:
                    idx = i
                    break
            if idx is None:
                raise AdjustError(
                    f"anchor_date {anchor_date} 超出数据范围",
                    context={"range": [factors[0].date, factors[-1].date]},
                )
            base_price = factors[idx].price_factor
            base_vol = factors[idx].volume_factor
        else:
            raise AdjustError(f"未知复权方式: {method}")

        if base_price == 0 or base_vol == 0:
            raise AdjustError("基准因子为零，无法归一化", context={"method": method})

        out: list[Bar] = []
        for bar, f in zip(bars, factors, strict=False):
            rp = f.price_factor / base_price
            rv = f.volume_factor / base_vol
            new_bar = Bar(
                datetime=bar.datetime,
                open=round(bar.open * rp, self.round_price),
                high=round(bar.high * rp, self.round_price),
                low=round(bar.low * rp, self.round_price),
                close=round(bar.close * rp, self.round_price),
                volume=int(round(bar.volume * rv)) if self.adjust_volume else bar.volume,
                amount=bar.amount,
                extra=dict(bar.extra or {}),
            )
            new_bar.extra["adj_price_factor"] = round(rp, 8)
            new_bar.extra["adj_volume_factor"] = round(rv, 8)
            out.append(new_bar)
        return out


def to_adjusted(
    bars: Sequence[Bar],
    events: Iterable[CapitalChange],
    method: str = AdjustMethod.QFQ,
    *,
    anchor_date: str | None = None,
    adjust_volume: bool = True,
) -> list[Bar]:
    """函数式入口，等价于 :meth:`AdjustEngine.apply`。"""
    return AdjustEngine(adjust_volume=adjust_volume).apply(
        bars, events, method, anchor_date=anchor_date
    )
