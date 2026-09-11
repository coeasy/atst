# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""东财筹码分布分析（资金流驱动，收集/发散判定）。

补齐 tstdx 此前缺失的 **筹码分布** 数据域——提示词模板维度 8「资金面与筹码
分布」的缺口。tstdx 无筹码数据时，无法判断主力是否在吸筹或派发。

传统筹码分布图（CYQ）需 F10 页面 JS 渲染，无公开 JSON API。本模块采用
**资金流驱动** 的替代方案——基于 push2 资金流接口的**主力净流入/流出**与
**大单/超大单占比**，量化筹码集中度变化：

* ``accumulation_ratio`` = (主力净流入 + 大单净流入) / 总成交额
  — 正值=筹码集中（主力吸筹），负值=筹码分散（主力派发）
* ``super_large_ratio`` = 超大单净流入 / 总成交额
* ``main_force_ratio`` = 主力净流入 / 总成交额

接口事实（2026-09-11 抓包验证，tstdx 自有实现）::

    # 实时资金流（当日）
    https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
      ?secid=1.600519&fields1=f1,f2,f3,f7
      &fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65
      &klt=101&lmt=1

    // 成功:
    {"rc":0,"data":{"code":"600519","name":"贵州茅台",
     "tradePeriods":{...},
     "klines":["2026-09-10,-374051552.0,-75805.0,374127360.0,
               -149640400.0,-224411152.0"]}}
    // klines 字段顺序:
    //   f51: 日期, f52: 主力净流入, f53: 小单净流入, f54: 大单净流入,
    //   f55: 超大单净流入, f56: 中单净流入, f57: 收盘, f58: 涨跌幅,
    //   f59: 主力净占比, f60: 小单净占比, f61: 大单净占比,
    //   f62: 超大单净占比, f63: 中单净占比, f64: 成交额, f65: 换手率

    # 历史资金流（多日）
    https://push2his.eastmoney.com/api/qt/stock/fflow/kline/get
      ?secid=1.600519&fields1=f1,f2,f3,f7
      &fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65
      &klt=101&lmt=30&end=20500101

.. note::
   本模块走 push2 / push2his 主机池（与 :mod:`tstdx.web.adapters`
   的 :class:`EastmoneySource` 同源），但响应结构为 ``{rc,data:{klines}}``
   而非 ``{data:{list}}``，故独立实现解析逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import SourceDeprecated, WebSourceError
from .base import _EastmoneyJson, num_f as _f
from .sources import EASTMONEY

__all__ = ["EastmoneyChipDistributionSource"]

#: push2 资金流 kline 字段顺序（2026-09-11 抓包验证）
_FFlow_FIELDS = [
    "date",           # f51
    "main_net",       # f52  主力净流入
    "small_net",      # f53  小单净流入
    "large_net",      # f54  大单净流入
    "super_large_net",# f55  超大单净流入
    "medium_net",     # f56  中单净流入
    "close",          # f57
    "change_pct",     # f58
    "main_ratio",     # f59  主力净占比
    "small_ratio",    # f60  小单净占比
    "large_ratio",    # f61  大单净占比
    "super_large_ratio", # f62  超大单净占比
    "medium_ratio",   # f63  中单净占比
    "amount",         # f64  成交额
    "turnover_rate",  # f65  换手率
]

_FIELDS1 = "f1,f2,f3,f7"
_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"


def _s(value: Any) -> str:
    """空值转空串，其余转 str。"""
    return "" if value is None else str(value)


def _to_secid(symbol: str) -> str:
    """转东财 secid：沪市 ``1.600519``，深市/北交所 ``0.000001``。"""
    from ..domain.symbol import split_symbol

    market, code = split_symbol(symbol)
    prefix = "1" if market == "sh" else "0"
    return f"{prefix}.{code}"


class EastmoneyChipDistributionSource(_EastmoneyJson):
    """东财筹码分布分析（资金流驱动）。

    基于 push2 资金流接口的主力净流入/流出数据，计算筹码集中度指标：

    * ``accumulation_ratio``: 主力+大单净流入占比（>0=吸筹，<0=派发）
    * ``main_force_ratio``: 主力净流入占比
    * ``super_large_ratio``: 超大单净流入占比
    * ``concentration_trend``: 近期趋势（accumulating/dispersing/neutral）
    """

    BASE = "https://push2his.eastmoney.com"
    HOSTS = (
        "https://push2his.eastmoney.com",
        "https://push2.eastmoney.com",
    )
    JSON_LABEL = "筹码分布"

    @property
    def source_name(self) -> str:
        return EASTMONEY

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        secid = _to_secid(symbols[0] if symbols else "sh600519")
        lmt = int(kwargs.get("days", 5))
        return (
            f"/api/qt/stock/fflow/kline/get"
            f"?secid={secid}&fields1={_FIELDS1}&fields2={_FIELDS2}"
            f"&klt=101&lmt={max(1, min(lmt, 60))}&end=20500101"
        )

    def fetch_chip_distribution(
        self,
        symbol: str,
        *,
        days: int = 5,
    ) -> dict[str, Any] | None:
        """获取个股筹码分布分析（资金流驱动）。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀（``600519`` / ``sh600519``）。
        days:
            分析天数（1-60，默认 5）。

        Returns
        -------
        ``{"symbol","name","period","total_amount","main_net_total",
        "main_net_ratio","super_large_net_total","super_large_ratio",
        "accumulation_ratio","concentration_trend","daily":[
        {"date","main_net","large_net","super_large_net","medium_net",
         "small_net","close","change_pct","amount","main_ratio",
         "accumulation_ratio"}, ...]} | None``

        金额单位为元（与东财原始口径一致）。``accumulation_ratio`` 为
        (主力净流入 + 大单净流入) / 成交额，正值=筹码集中（吸筹），
        负值=筹码分散（派发）。
        """
        self.rate_limiter.acquire(self.source_name)
        url = self.build_url([symbol], days=days)
        payload = self._get_json(url)
        return self._parse_chip_distribution(payload, symbol=symbol)

    def fetch_chip_distributions(
        self,
        symbols: Sequence[str],
        *,
        days: int = 5,
        max_count: int = 20,
    ) -> list[dict[str, Any]]:
        """批量获取筹码分布分析（逐只查询，带限流）。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..domain.symbol import split_symbol

        symbols = [s for s in symbols][:max_count]
        results: list[dict[str, Any]] = []

        def _fetch_one(sym: str) -> dict[str, Any] | None:
            try:
                return self.fetch_chip_distribution(sym, days=days)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=min(5, len(symbols))) as pool:
            futures = {pool.submit(_fetch_one, s): s for s in symbols}
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    results.append(r)

        # 按 accumulation_ratio 降序排列
        results.sort(
            key=lambda r: r.get("accumulation_ratio") or 0, reverse=True
        )
        return results

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _parse_chip_distribution(
        self, payload: Any, *, symbol: str
    ) -> dict[str, Any] | None:
        """解析 push2 资金流 kline 响应，计算筹码集中度指标。"""
        if payload.get("rc") != 0:
            return None
        data = payload.get("data") or {}
        code = _s(data.get("code"))
        name = _s(data.get("name"))
        klines = data.get("klines") or []
        if not klines:
            return None

        daily: list[dict[str, Any]] = []
        total_amount = 0.0
        total_main_net = 0.0
        total_large_net = 0.0
        total_super_large_net = 0.0

        for kline_str in klines:
            parts = kline_str.split(",")
            if len(parts) < 15:
                continue
            row: dict[str, Any] = {}
            for i, field in enumerate(_FFlow_FIELDS):
                val = _f(parts[i]) if i > 0 else parts[i]
                row[field] = val if i > 0 else str(val)

            date = str(row.get("date", ""))
            main_net = row.get("main_net") or 0.0
            large_net = row.get("large_net") or 0.0
            super_large_net = row.get("super_large_net") or 0.0
            amount = row.get("amount") or 0.0

            # 计算当日 accumulation_ratio
            accum_ratio = None
            if amount > 0:
                accum_ratio = round((main_net + large_net) / amount * 100, 4)

            daily.append({
                "date": date,
                "main_net": main_net,
                "large_net": large_net,
                "super_large_net": super_large_net,
                "medium_net": row.get("medium_net") or 0.0,
                "small_net": row.get("small_net") or 0.0,
                "close": row.get("close") or 0.0,
                "change_pct": row.get("change_pct") or 0.0,
                "amount": amount,
                "turnover_rate": row.get("turnover_rate") or 0.0,
                "main_ratio": row.get("main_ratio") or 0.0,
                "accumulation_ratio": accum_ratio,
            })

            total_amount += amount
            total_main_net += main_net
            total_large_net += large_net
            total_super_large_net += super_large_net

        # 汇总指标
        total_accum_ratio = None
        if total_amount > 0:
            total_accum_ratio = round(
                (total_main_net + total_large_net) / total_amount * 100, 4
            )
        total_main_ratio = (
            round(total_main_net / total_amount * 100, 4) if total_amount > 0 else None
        )
        total_super_large_ratio = (
            round(total_super_large_net / total_amount * 100, 4)
            if total_amount > 0 else None
        )

        # 趋势判定：对比最近 1/3 与最早 1/3 的 accumulation_ratio
        trend = "neutral"
        if len(daily) >= 3:
            recent = daily[-max(1, len(daily) // 3):]
            early = daily[:max(1, len(daily) // 3)]
            recent_ratio = (
                sum(d.get("accumulation_ratio") or 0 for d in recent) / len(recent)
            )
            early_ratio = (
                sum(d.get("accumulation_ratio") or 0 for d in early) / len(early)
            )
            diff = recent_ratio - early_ratio
            if diff > 2.0:
                trend = "accumulating"
            elif diff < -2.0:
                trend = "dispersing"

        # 确定分析期间
        dates = [d["date"] for d in daily if d.get("date")]
        period = (
            f"{dates[0]}~{dates[-1]}" if len(dates) >= 2 else (dates[0] if dates else "")
        )

        return {
            "symbol": code,
            "name": name,
            "period": period,
            "total_amount": round(total_amount, 2),
            "main_net_total": round(total_main_net, 2),
            "main_net_ratio": total_main_ratio,
            "super_large_net_total": round(total_super_large_net, 2),
            "super_large_ratio": total_super_large_ratio,
            "accumulation_ratio": total_accum_ratio,
            "concentration_trend": trend,
            "daily": daily,
        }
