# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""领域数据模型（§9）。

全局单位约定（**不可协商**）::

    价格     元（float）
    成交量   股（int）—— HTTP Web 源与期货品种会在此归一化
    成交额   元（float）
    时间     交易所本地时间的字符串（日线为日期，分钟线带时分），出口处不做时区换算

输出三态::

    dict      调试/序列化友好
    tuple     遍历最快（比 dict 快 40~60%）
    DataFrame 分析与回测（需 pandas extra）
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "Bar",
    "Quote",
    "Tick",
    "MinutePoint",
    "CapitalChange",
    "AuctionSnapshot",
    "Level",
    "to_dicts",
    "to_dicts_lazy",
    "to_tuples",
    "to_dataframe",
    "BAR_FIELDS",
    "QUOTE_FIELDS",
    "TICK_FIELDS",
]

BAR_FIELDS = ("datetime", "open", "high", "low", "close", "volume", "amount")
QUOTE_FIELDS = (
    "code",
    "datetime",
    "price",
    "last_close",
    "open",
    "high",
    "low",
    "volume",
    "amount",
    "bid",
    "ask",
)
TICK_FIELDS = ("time", "price", "volume", "num", "buyorsell")


@dataclass(slots=True)
class Level:
    """盘口一档。"""

    price: float = 0.0
    volume: int = 0


@dataclass(slots=True)
class Bar:
    """K 线 / 分钟线。"""

    datetime: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    amount: float = 0.0
    #: 期货持仓量 / 期权希腊字母等扩展字段
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in BAR_FIELDS}
        if self.extra:
            d.update(self.extra)
        return d

    def to_tuple(self) -> tuple:
        return tuple(getattr(self, k) for k in BAR_FIELDS)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bar:
        known = {k: v for k, v in d.items() if k in BAR_FIELDS}
        extra = {k: v for k, v in d.items() if k not in BAR_FIELDS}
        return cls(**known, extra=extra)


@dataclass(slots=True)
class Quote:
    """实时行情快照（含五档）。"""

    code: str = ""
    #: 行情时间（本地字符串，可为空——部分 Web 源不返回）
    datetime: str | None = None
    price: float = 0.0
    last_close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume: int = 0
    amount: float = 0.0
    bid: list[Level] = field(default_factory=list)
    ask: list[Level] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def change(self) -> float:
        return self.price - self.last_close if self.last_close else 0.0

    @property
    def pct_change(self) -> float:
        return (self.change / self.last_close * 100) if self.last_close else 0.0

    @property
    def turnover_rate(self) -> float:
        """换手率（%），需 extra 提供 float_shares（流通股本）。"""
        shares = self.extra.get("float_shares")
        if not shares:
            return 0.0
        return self.volume / float(shares) * 100

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in QUOTE_FIELDS if k not in ("bid", "ask")}
        d["bid"] = [asdict(x) for x in self.bid]
        d["ask"] = [asdict(x) for x in self.ask]
        if self.extra:
            d.update(self.extra)
        return d


@dataclass(slots=True)
class Tick:
    """逐笔成交。"""

    time: str = ""
    price: float = 0.0
    volume: int = 0
    #: 成交笔数序号（同一时刻多笔时递增）
    num: int = 0
    #: 买卖方向：0=买 1=卖 2=中性
    buyorsell: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in TICK_FIELDS}


@dataclass(slots=True)
class MinutePoint:
    """分时点。"""

    time: str = ""
    price: float = 0.0
    avg_price: float = 0.0
    volume: int = 0
    amount: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapitalChange:
    """除权除息 / 股本变迁事件（0x000F）。"""

    code: str = ""
    market: int = 0
    category: int = 0
    category_name: str = ""
    date: str = ""
    #: 每 10 股派息（元）
    dividend: float = 0.0
    #: 配股价（元）
    rights_price: float = 0.0
    #: 每 10 股送转
    bonus_ratio: float = 0.0
    #: 每 10 股配股
    rights_ratio: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AuctionSnapshot:
    """集合竞价快照（0x056A）。"""

    code: str = ""
    time: str = ""
    price: float = 0.0
    volume: int = 0
    amount: float = 0.0
    ref_price: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 输出三态转换
# --------------------------------------------------------------------------- #
def to_dicts_lazy(items: Sequence[Any]) -> Iterator[dict[str, Any]]:
    """惰性生成器版 :func:`to_dicts`（M2）。

    大批量（全市场/长 K 线）场景下避免一次性构造完整 list 的中间拷贝；
    调用方按需消费即逐条产出，内存峰值仅 1 条。
    """
    for x in items:
        yield x.to_dict() if hasattr(x, "to_dict") else dict(x)


def to_dicts(items: Sequence[Any]) -> list[dict[str, Any]]:
    """转为 list[dict]（批量消费场景）。惰性版见 :func:`to_dicts_lazy`。"""
    return list(to_dicts_lazy(items))


def to_tuples(items: Sequence[Any]) -> list[tuple]:
    out: list[tuple] = []
    for x in items:
        if hasattr(x, "to_tuple"):
            out.append(x.to_tuple())
        elif isinstance(x, dict):
            out.append(tuple(x.values()))
        else:
            out.append(tuple(asdict(x).values()))
    return out


def to_dataframe(items: Sequence[Any], *, columns: Sequence[str] | None = None):
    """转为 pandas DataFrame（需 ``pip install atst[dataframe]``）。"""
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover
        from ..errors import DependencyMissingError

        raise DependencyMissingError(
            "DataFrame 输出需要 pandas: pip install 'atst[dataframe]'", cause=exc
        ) from exc

    rows = to_dicts(items)
    df = pd.DataFrame(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.set_index("datetime")
    return df
