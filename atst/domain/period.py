# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical bar-period vocabulary shared by every public face.

Equivalent public spellings must collapse before Provider selection and
fingerprint construction, otherwise the same request can acquire different
identities in different layers.

本模块是周期词表的**唯一声明处**：:data:`CANONICAL_PERIODS` 是规范拼写，
:data:`PERIOD_ALIASES` 是"用户可能怎么打字"到规范拼写的映射。下游每张表都从这两份
派生，不再各自手抄别名——第 18 轮普查量到，手写别名表一旦存在，不同公开面接受的
拼写集就会分叉（``Client.bars(period="1y")`` 报 ``ParseError``，而同一份数据经
:func:`normalize_bar_period` 的路径接受它），分叉出去的 15 个拼写是这次登记的账。

* :data:`CANONICAL_PERIODS` —— 规范拼写，也是档案层 ``atst.reader.profile.Period``
  成员取值的唯一许可集（由判据钉住，两边不许各自长词）。
* :data:`PERIOD_ALIASES` —— 别名 → 规范拼写。**只允许**映射到
  :data:`CANONICAL_PERIODS` 里的值：`"quarter" -> "season"` 这类"别名指向另一个别名"
  正是 G13 那格腐烂的形状。
"""

from __future__ import annotations

__all__ = ["CANONICAL_PERIODS", "MINUTE_PERIODS", "PERIOD_ALIASES", "normalize_bar_period"]

#: 规范周期拼写：库内比较、档案声明与下游每张派生表都只用这些写法。
#: ``tick`` 没有 K 线 category（分笔走另一条命令），所以它是唯一不进编号表的规范周期。
CANONICAL_PERIODS: tuple[str, ...] = (
    "tick",
    "1min",
    "5min",
    "15min",
    "30min",
    "60min",
    "day",
    "week",
    "month",
    "season",
    "year",
)

#: 分钟级周期（决定走 minute_kline 还是 kline 通道），由规范拼写派生。
MINUTE_PERIODS: frozenset[str] = frozenset(p for p in CANONICAL_PERIODS if p.endswith("min"))

#: 公开别名 → 规范拼写。键不许是 :data:`CANONICAL_PERIODS` 的成员（那由派生补齐）。
PERIOD_ALIASES: dict[str, str] = {
    "min": "1min",
    "1m": "1min",
    "m1": "1min",
    "5m": "5min",
    "m5": "5min",
    "15m": "15min",
    "m15": "15min",
    "30m": "30min",
    "m30": "30min",
    "60m": "60min",
    "m60": "60min",
    "1h": "60min",
    "1hour": "60min",
    "d": "day",
    "daily": "day",
    "1d": "day",
    "w": "week",
    "weekly": "week",
    "1w": "week",
    "m": "month",
    "mo": "month",
    "monthly": "month",
    "1mo": "month",
    "q": "season",
    "quarter": "season",
    "quarterly": "season",
    "y": "year",
    "1y": "year",
    "yearly": "year",
}


def normalize_bar_period(period: str | None) -> str:
    """Return the canonical spelling for a public bar-period alias."""
    key = (period or "day").strip().lower()
    return PERIOD_ALIASES.get(key, key)
