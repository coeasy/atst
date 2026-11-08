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

from array import array
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
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

# --------------------------------------------------------------------------- #
# extra 低开销容器
#
# 每条 Bar / Quote 都带一份扩展字段。常见情况是「没有扩展字段」（K 线）或只有
# 一个 ``prev_close`` / ``open_interest``（vipdoc），为每对象分配 dict 的常数开销
# 在大批量驻留时很可观。这里分三层存放：
#
# * ``None``                       —— 空 extra，零底层存储；
# * ``tuple``（扁平 k1,v1,k2,v2）  —— 保持插入顺序，比 dict 小得多；
# * ``dict``                       —— 首次被当作 dict 使用时才物化，之后留在槽位里。
#
# 公开属性 ``extra`` 始终是 ``dict`` 实例（``isinstance(bar.extra, dict)`` 为真），
# 但空态与「从未访问」态都不分配实体 dict；``to_dict`` 直读槽位，不触发物化。
# --------------------------------------------------------------------------- #
_DT_CACHE_MAX = 4096
_DT_CACHE: dict[str, str] = {}
_DT_CACHE_FULL = False

#: ``extra`` 的合法输入：映射 / 已压平的内部 tuple。
ExtraInput = Mapping[str, Any] | tuple[Any, ...] | None

#: ``extra`` 的内部存放形态：空 ``None`` / 扁平 tuple / 已物化的 dict。
ExtraStorage = tuple[Any, ...] | dict[str, Any] | None


def _canonical_datetime(value: str) -> str:
    """同一批多标的的时间字符串高度重复（长 K 线尤其明显），做有界去重。

    字符串不可变，返回的仍是等值 ``str``；去重表上限到顶后只停止新增（并且
    不再查表，避免为高频唯一时间戳持续支付哈希成本），不会无界增长。
    """
    global _DT_CACHE_FULL
    if not value or _DT_CACHE_FULL:
        return value
    cached = _DT_CACHE.get(value)
    if cached is not None:
        return cached
    if len(_DT_CACHE) >= _DT_CACHE_MAX:
        _DT_CACHE_FULL = True
        return value
    _DT_CACHE[value] = value
    return value


def _flatten_extra(extra: ExtraInput) -> ExtraStorage:
    """把 mapping 压成扁平 tuple；空映射返回 ``None``。

    ``tuple`` 视为**已压平**的内部输入（解析热路径直接给出，避免每行构造 dict
    后再拆开），这条分支必须排在最前。``dict`` 等映射一律压平——直接留着调用方
    的 dict 会把每对象一个实体 dict 的常数开销带回来。
    """
    if not extra:
        return None
    if isinstance(extra, tuple):
        return extra
    out: list[Any] = []
    for key, value in extra.items():
        out.append(key)
        out.append(value)
    return tuple(out)


def _extra_dict(stored: ExtraStorage) -> dict[str, Any]:
    """把槽位里的任一形态快照成普通 dict（不改动对象状态）。"""
    if not stored:
        return {}
    if isinstance(stored, dict):
        return dict(stored)
    return {stored[i]: stored[i + 1] for i in range(0, len(stored), 2)}


def _extra_get(stored: ExtraStorage, key: str, default: Any = None) -> Any:
    """读单个扩展字段而不物化 dict（批量读属性时保持零分配）。"""
    if not stored:
        return default
    if isinstance(stored, dict):
        return stored.get(key, default)
    for i in range(0, len(stored), 2):
        if stored[i] == key:
            return stored[i + 1]
    return default


@dataclass(slots=True)
class Level:
    """盘口一档。"""

    price: float = 0.0
    volume: int = 0


@dataclass(init=False, eq=False, repr=False)
class Bar:
    """K 线 / 分钟线。

    仍是 dataclass（``is_dataclass`` / ``fields`` / ``asdict`` 可用）；槽位由手写
    ``__slots__`` 提供，不产生 ``__dict__``。``extra`` 空态存 ``None``、非空先以
    扁平 ``tuple`` 存放，首次被当作 dict 使用时物化为 ``dict`` 并留在槽位里；
    对外访问到的始终是 ``dict`` 实例。
    """

    __slots__ = (*BAR_FIELDS, "_extra")

    datetime: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    #: 期货持仓量 / 期权希腊字母等扩展字段（对外恒为 dict）
    extra: dict[str, Any]

    def __init__(
        self,
        datetime: str = "",
        open: float = 0.0,
        high: float = 0.0,
        low: float = 0.0,
        close: float = 0.0,
        volume: int = 0,
        amount: float = 0.0,
        #: 期货持仓量 / 期权希腊字母等扩展字段
        extra: ExtraInput = None,
    ) -> None:
        self.datetime = _canonical_datetime(datetime)
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.amount = amount
        self._extra: ExtraStorage = _flatten_extra(extra)

    @property  # type: ignore[no-redef]
    def extra(self) -> dict[str, Any]:
        stored = self._extra
        if isinstance(stored, dict):
            return stored
        materialized = _extra_dict(stored)
        self._extra = materialized
        return materialized

    @extra.setter
    def extra(self, value: ExtraInput) -> None:
        self._extra = _flatten_extra(value)

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in BAR_FIELDS}
        stored = self._extra
        if stored:
            if isinstance(stored, dict):
                d.update(stored)
            else:
                for i in range(0, len(stored), 2):
                    d[stored[i]] = stored[i + 1]
        return d

    def to_tuple(self) -> tuple:
        return tuple(getattr(self, k) for k in BAR_FIELDS)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Bar:
        known = {k: v for k, v in d.items() if k in BAR_FIELDS}
        extra = {k: v for k, v in d.items() if k not in BAR_FIELDS}
        return cls(**known, extra=extra)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Bar):
            return NotImplemented
        return self.to_tuple() == other.to_tuple() and _extra_dict(self._extra) == _extra_dict(
            other._extra
        )

    def __repr__(self) -> str:
        parts = [f"{k}={getattr(self, k)!r}" for k in BAR_FIELDS]
        parts.append(f"extra={_extra_dict(self._extra)!r}")
        return f"Bar({', '.join(parts)})"


def _pack_levels(levels: Sequence[Any] | None) -> array | None:
    """把 ``list[Level]`` / 行式 dict 压成 ``array('d')``（空则 ``None``）。

    盘口五档在大批量行情驻留时占大头；只保留价格与量的数值本身，不驻留
    ``Level`` 实例与独立的 float/int 对象。公开接口仍按需重建 ``list[Level]``。
    """
    if not levels:
        return None
    buf = array("d")
    append = buf.append
    for level in levels:
        if isinstance(level, Level):
            append(level.price)
            append(level.volume)
        elif isinstance(level, Mapping):
            append(level.get("price", 0.0))
            append(level.get("volume", 0) or 0)
        else:
            append(getattr(level, "price", 0.0))
            append(getattr(level, "volume", 0))
    return buf


def _unpack_levels(flat: array | None) -> list[Level]:
    if not flat:
        return []
    return [Level(flat[i], int(flat[i + 1])) for i in range(0, len(flat), 2)]


@dataclass(init=False, eq=False, repr=False)
class Quote:
    """实时行情快照（含五档）。

    ``bid`` / ``ask`` 内部以 ``array('d')`` 紧凑保存价格/量，首次访问时重建为
    ``list[Level]`` 并留在对象槽位里复用；未访问的对象不驻留 Level 实例（五档是
    大批量行情的主要常数开销）。``extra`` 与 :class:`Bar` 同构：空态 ``None``、
    未访问态扁平 ``tuple``、对外恒为 ``dict``；类本身仍是 dataclass。
    """

    __slots__ = (
        "code",
        "datetime",
        "price",
        "last_close",
        "open",
        "high",
        "low",
        "volume",
        "amount",
        "_bid_raw",
        "_ask_raw",
        "_bid_list",
        "_ask_list",
        "_extra",
    )

    code: str
    datetime: str | None
    price: float
    last_close: float
    open: float
    high: float
    low: float
    volume: int
    amount: float
    bid: list[Level]
    ask: list[Level]
    extra: dict[str, Any]

    def __init__(
        self,
        code: str = "",
        #: 行情时间（本地字符串，可为空——部分 Web 源不返回）
        datetime: str | None = None,
        price: float = 0.0,
        last_close: float = 0.0,
        open: float = 0.0,
        high: float = 0.0,
        low: float = 0.0,
        volume: int = 0,
        amount: float = 0.0,
        bid: Sequence[Any] | None = None,
        ask: Sequence[Any] | None = None,
        extra: ExtraInput = None,
    ) -> None:
        self.code = code
        self.datetime = _canonical_datetime(datetime) if isinstance(datetime, str) else datetime
        self.price = price
        self.last_close = last_close
        self.open = open
        self.high = high
        self.low = low
        self.volume = volume
        self.amount = amount
        self._bid_raw: array | None = _pack_levels(bid)
        self._ask_raw: array | None = _pack_levels(ask)
        self._bid_list: list[Level] | None = None
        self._ask_list: list[Level] | None = None
        self._extra: ExtraStorage = _flatten_extra(extra)

    @property  # type: ignore[no-redef]
    def bid(self) -> list[Level]:
        levels = self._bid_list
        if levels is None:
            levels = _unpack_levels(self._bid_raw)
            self._bid_list = levels
        return levels

    @bid.setter
    def bid(self, value: Sequence[Any] | None) -> None:
        self._bid_raw = _pack_levels(value)
        self._bid_list = None

    @property  # type: ignore[no-redef]
    def ask(self) -> list[Level]:
        levels = self._ask_list
        if levels is None:
            levels = _unpack_levels(self._ask_raw)
            self._ask_list = levels
        return levels

    @ask.setter
    def ask(self, value: Sequence[Any] | None) -> None:
        self._ask_raw = _pack_levels(value)
        self._ask_list = None

    @property  # type: ignore[no-redef]
    def extra(self) -> dict[str, Any]:
        stored = self._extra
        if isinstance(stored, dict):
            return stored
        materialized = _extra_dict(stored)
        self._extra = materialized
        return materialized

    @extra.setter
    def extra(self, value: ExtraInput) -> None:
        self._extra = _flatten_extra(value)

    @property
    def change(self) -> float:
        return self.price - self.last_close if self.last_close else 0.0

    @property
    def pct_change(self) -> float:
        return (self.change / self.last_close * 100) if self.last_close else 0.0

    @property
    def turnover_rate(self) -> float:
        """换手率（%），需 extra 提供 float_shares（流通股本）。"""
        shares = _extra_get(self._extra, "float_shares")
        if not shares:
            return 0.0
        return self.volume / float(shares) * 100

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in QUOTE_FIELDS if k not in ("bid", "ask")}
        #: 空盘口不触发 Level 重建（序列化批量行情时避免无谓分配）。
        if self._bid_list is None and self._bid_raw is None:
            d["bid"] = []
        else:
            d["bid"] = [asdict(x) for x in self.bid]
        if self._ask_list is None and self._ask_raw is None:
            d["ask"] = []
        else:
            d["ask"] = [asdict(x) for x in self.ask]
        stored = self._extra
        if stored:
            #: 下划线前缀是**内部字段**标记（协议原始留存 / 漂移判据等）：
            #: 公开 dict 出口只扁平化契约字段，内部键留在 ``extra`` 里给对象消费者——
            #: 否则 TDX 的原始解码残留（_hdr/_u4/_v2/tail_leb128/_warnings）
            #: 会平铺进行情契约，DataFrame/CSV 的列名一起被污染。
            if isinstance(stored, dict):
                for key, value in stored.items():
                    if not key.startswith("_"):
                        d[key] = value
            else:
                for i in range(0, len(stored), 2):
                    key = stored[i]
                    if not key.startswith("_"):
                        d[key] = stored[i + 1]
        return d

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quote):
            return NotImplemented
        return (
            self.code == other.code
            and self.datetime == other.datetime
            and self.price == other.price
            and self.last_close == other.last_close
            and self.open == other.open
            and self.high == other.high
            and self.low == other.low
            and self.volume == other.volume
            and self.amount == other.amount
            and self.bid == other.bid
            and self.ask == other.ask
            and _extra_dict(self._extra) == _extra_dict(other._extra)
        )

    def __repr__(self) -> str:
        parts = [f"{k}={getattr(self, k)!r}" for k in QUOTE_FIELDS if k not in ("bid", "ask")]
        parts.insert(QUOTE_FIELDS.index("bid"), f"bid={self.bid!r}")
        parts.insert(QUOTE_FIELDS.index("ask"), f"ask={self.ask!r}")
        parts.append(f"extra={_extra_dict(self._extra)!r}")
        return f"Quote({', '.join(parts)})"


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
