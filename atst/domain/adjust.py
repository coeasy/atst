# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""复权引擎（§11）：前复权 / 后复权 / 定点复权。

**定位：生产已接线。** 消费方是 ``derived`` Provider ``adjustment`` channel 的
``adjusted_bars`` 能力——经 :meth:`DirectProviderExecutor._composed_call` 走到
:class:`AdjustEngine`，``Client.call("adjusted_bars", ...)`` 与 ``atst adjusted-bars``
子命令都落在这条链上。

.. note::
   本模块 docstring 曾经写着"无生产链路消费（未接入 client/facade 降级链）"，那是
   v16 之前的史实：``atst.facade`` 已随 v16 Phase 2 物理删除，v13 契约本身也禁止降级
   链。文档与代码分叉会让"能不能用"这个问题只能靠读源码回答，故在此更正。

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

**复权后 bar 的 ``extra``**：``apply`` 给每根 bar 写入 ``adj_price_factor`` /
``adj_volume_factor``（本根相对基准的因子）与 ``raw_volume``（**复权前**的成交量）。
宽表算换手率、量比时必须读 ``raw_volume``——复权后的 ``volume`` 与历史流通股本是两个
尺度，比值无意义（这个坑见 :func:`atst.domain.enrich.enrich_daily_bars`）。

**缺前收盘价的降级口径**：bar 未携带 ``extra["prev_close"]`` 时，价格因子
退化为 ``1/(1+S+R)``（只还原股本扩张，**忽略现金红利**）——现金红利会
因此丢失，引擎把这条瑕疵记进结果侧的告警通道（:func:`atst.diagnostics.record_warning`，
类别 ``ADJUST_PREV_CLOSE_MISSING``；见 :func:`_price_adjust_ratio`），stderr 只唠叨一次。
需要精确复权请确保事件携带前收盘价。

事件类别过滤
------------
``CapitalChange.category`` 中只有 **1（除权除息）** 参与价格因子计算；
其他类别（增发、回购、股本变化等）不影响除权价，仅记录在案。
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date as _date
from typing import Any

from ..diagnostics import WarningCode, record_warning
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
    closes = [_as_positive_close(b) for b in bars]

    for i, bar in enumerate(bars):
        d = bar_dates[i]
        # 应用所有 date <= 当前 bar 日期的事件
        # （除权日当天开盘价已反映除权，故事件日及之后的 bar 乘以因子）
        while ev_idx < n_ev and ev_dates[ev_idx] <= d:
            ev = ev_list[ev_idx]
            #: 前收盘价 = 该事件日**之前最后一根** bar 的收盘价（连续交易日的定义）。
            #: 过去这里写的是 ``bars[i-1].close``——拿"循环当前位置的前一根"冒充"事件
            #: 日的前一根"。两者只在「一个 bar 间隔内恰好只有一个事件」时相等：一旦
            #: 同一间隔里挤了多个事件（长停牌后连续除权），或事件早于窗口首根 bar
            #: （``i == 0``），除最早那个事件外全部拿到错误的基准价，而除权参考价
            #: ``(P - D + Pr·R)/(1+S+R)`` 对 P 是敏感的——复权因子于是系统性偏大。
            #: 这里按事件日做一次二分定位，每个事件都拿到它自己的前收盘。
            prev_close = _prev_close_before(bar_dates, closes, ev_dates[ev_idx])
            price_factor *= _price_adjust_ratio(
                ev,
                bar,
                fallback_prev_close=prev_close,
                event_before_window=ev_dates[ev_idx] < bar_dates[0],
            )
            vol_factor *= _volume_adjust_ratio(ev)
            ev_idx += 1
        factors.append(
            AdjustFactor(date=bar.datetime, price_factor=price_factor, volume_factor=vol_factor)
        )

    return factors


#: 「缺前收盘价忽略现金红利」的一次性告警旗标（once 语义）
_warned_missing_prev_close = False


def _as_positive_close(bar: Bar) -> float:
    """bar 收盘价的正值读数；缺失或 <= 0 一律记 0（不代表"有价格 0 元"）。

    停牌日 / 数据源缺列时 ``close`` 可能是 ``None``。它不能当基准价用，但也不能
    让二分定位因此失败——0 会被 :func:`_prev_close_before` 判为"不可用"，进而
    走「忽略现金红利」的降级口径并告警，而不是静默拿 0 去除。
    """
    value = getattr(bar, "close", None)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _prev_close_before(
    bar_dates: Sequence[_date],
    closes: Sequence[float],
    event_date: _date,
) -> float | None:
    """事件日之前最后一根 bar 的收盘价；没有则 ``None``。

    ``bisect_left`` 给出第一个 ``bar_date >= event_date`` 的下标，它前一位就是
    严格早于事件日的那根。返回 ``None`` 意味着该事件早于序列起点——调用方无法
    凭这段窗口还原它的基准价，只能走降级口径（:data:`WarningCode.ADJUST_PREV_CLOSE_MISSING`）。
    """
    idx = bisect_left(bar_dates, event_date)
    if idx <= 0:
        return None
    value = closes[idx - 1]
    return float(value) if value > 0 else None


def _price_adjust_ratio(
    ev: CapitalChange,
    bar: Bar,
    fallback_prev_close: float | None = None,
    *,
    event_before_window: bool = False,
) -> float:
    """返回 :math:`1/k`（后复权方向的价格放大系数）。

    前收盘价的取值顺序：

    1. ``bar.extra["prev_close"]``——数据自带，最准；
    2. ``fallback_prev_close``——**上一根 bar 的收盘价**。连续交易日里"前收盘价"按
       定义就是上一交易日的收盘价，所以 K 线序列本身就能提供它。过去只用第 1 条，
       而 TDX / 东财的 K 线都不带这个字段，于是**每一次复权都在走降级口径**：现金
       红利被整项忽略，价格因子退化成 ``1/(1+S+R)``。对 10 送 8 转 12 派 39.74 这种
       事件，跌幅里属于派息的那部分被当成了股本扩张，因子偏大。
    3. 都没有 → 退化为 ``1/(1+S+R)``，现金红利被忽略，误差 = D/P 量级。首次命中时发
       一次性 :class:`UserWarning` 提示（docstring 契约与实际行为此前不一致，审计
       §3-7）。
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
    if prev_close is None and fallback_prev_close and fallback_prev_close > 0:
        prev_close = float(fallback_prev_close)

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
    if d:
        # 去重只管 stderr（一次进程说一次），结果侧的瑕疵逐次都要记（F-45）。
        fresh = not _warned_missing_prev_close
        _warned_missing_prev_close = True
        #: 两种「拿不到前收盘」的成因要分开说：早于窗口起点是**调用方能修**的
        #: （加大 count），窗口内却取不到是**数据缺列**。混成一句，调用方就不知
        #: 道该改参数还是该换源。前复权下这条对结果无影响（窗口前的事件给每根
        #: bar 乘的是同一个常数，归一化时约掉），后复权会有 d/P 量级偏差。
        cause = (
            f"该事件早于本次 K 线窗口起点（{bar.datetime}），窗口内没有它的前一根 K 线"
            "——加大 count 覆盖该事件即可拿到精确值"
            if event_before_window
            else "数据源未提供前收盘价，且窗口内该事件前没有可用收盘价"
        )
        record_warning(
            WarningCode.ADJUST_PREV_CLOSE_MISSING,
            f"复权事件 {ev.date} 缺少前收盘价（bar.extra['prev_close']），"
            f"每股现金红利 {d:.4f} 元被忽略：价格因子按 1/(1+S+R) 近似。{cause}。"
            "精确复权请提供前收盘价。",
            stacklevel=3,
            stderr=fresh,
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
            #: 原始成交量留一份。**复权后的 ``volume`` 不能再拿来算换手率**：
            #: 分子乘了 ``rv``、分母（流通股本）没乘，两者不同尺度，比值就废了。
            #: 宽表（:mod:`atst.domain.enrich`）的 ``turnover`` / ``vol_ratio`` 因此
            #: 必须读这一份。留整数原值而不是「除以 rv 反推」是为了不受取整误差影响。
            new_bar.extra["raw_volume"] = int(bar.volume)
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
