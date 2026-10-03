"""标的类别表 —— ``atst.universe`` 的单一事实源。

这一层回答三个问题：

1. **一个代码属于哪一类**（:func:`classify`）；
2. **这一类从哪拿**（:data:`SINA_NODES` / :attr:`AssetClass.tdx_segments`，见
   :class:`AssetClass`；节点反向索引是 :data:`NODE_TO_CLASSES`）；
3. **拿到了要落到哪**——类别与 :mod:`scripts.sync_daily_history` 的目录、
   ``0x052D`` 指数位是同一套名字，两处共用这份表，避免"脚本说 ETF 归 ETF
   目录、清单说 ETF 归别处"这种对不上的事。

类别口径全部来自**实测**（2026-10-03）：新浪 ``Market_Center`` 的 ``hs_a``
/ ``sh_a`` / ``sz_a`` / ``cyb`` / ``kcb`` / ``hs_b`` 六个节点实测有货，
``etf_hq`` / ``bond_hq`` / ``index`` 等十几个候选节点实测返回空——所以 ETF /
LOF / 可转债 / 指数**没有**新浪节点可走，只能走 tdx 段表探测（已经实测
跑出过 8385 只六类标的）。

为什么没有东财 clist 作为第二源：clist 的 ``fs`` 参数映射（ETF/LOF/可转债
各自的 ``b:MKxxxx``）尚未真机校准，而本轮实测 push2 全站处于 IP 级软限流
（``Server disconnected``）。**没校准过的 ``fs`` 是不该写进代码表的**——
写进去就是一锅静默返回空或者直接报错的雷，比留一个明确的缺口诚实。
:mod:`atst.universe._sources` 因此只有两行 online 源，缺口的补法写在
:data:`TBD_SOURCES` 里（真机校准后再登记）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "ASSET_CLASSES",
    "NODE_TO_CLASSES",
    "SINA_NODES",
    "TBD_SOURCES",
    "AssetClass",
    "Security",
    "asset_class_names",
    "classify",
    "classify_with_index",
    "class_of_directory",
]


#: 一只标的。``code`` 用 atst 的带前缀写法（``sh600519``），不是裸 6 位。
#: 名称可能为空（段表探测路径不回名称），所以别把 ``name`` 当必填字段用。
@dataclass(frozen=True, slots=True)
class Security:
    """标的清单里的一行：代码 + 名称 + 类别。"""

    code: str
    name: str = ""
    kind: str = "stock"

    def __str__(self) -> str:
        return f"{self.code} {self.name}".strip()

    @property
    def symbol(self) -> str:
        """:attr:`code` 的别名——脚本那侧一律叫 ``symbol``，两个词指一件事。"""
        return self.code

    #: 交易所前缀（sh / sz / bj）。
    @property
    def market(self) -> str:
        head = self.code[:2].lower()
        return head if head in ("sh", "sz", "bj") else ""


#: 类别 → 落盘目录 / 0x052D 指数位（与 sync_daily_history 的类别目录一一对应）
_DIR_BY_KIND: Final[dict[str, str]] = {
    "stock": "stock",
    "bse": "bse",
    "etf": "etf",
    "lof": "lof",
    "bond": "bond",
    "index": "index",
    "bshare": "bshare",
}


@dataclass(frozen=True, slots=True)
class AssetClass:
    """一个标的类别：名字、中文名、取数入口、落盘目录、是否走指数位。"""

    name: str
    label: str
    #: 新浪行情中心节点；``None`` 表示新浪没有该类入口（只能走段表探测）。
    sina_node: str | None
    #: 段表探测用的代码前缀段。空元组表示探测不了（客户端直接拒）。
    tdx_segments: tuple[str, ...]
    #: tdx 0x052D 是否走指数位。走反了主站会回 ``5616-57-83`` 这种荒唐日期。
    index_bit: bool

    @property
    def directory(self) -> str:
        """落盘目录名，见 :data:`_DIR_BY_KIND`。"""
        return _DIR_BY_KIND[self.name]


#: 类别名 → 类别定义。顺序即 ``--list-class`` 的展示顺序。
ASSET_CLASSES: Final[tuple[AssetClass, ...]] = (
    AssetClass(
        name="stock",
        label="A 股（沪深主板 + 创业板 + 科创板）",
        sina_node="hs_a",
        tdx_segments=(
            "sh600",
            "sh601",
            "sh603",
            "sh605",
            "sh688",
            "sh689",
            "sz000",
            "sz001",
            "sz002",
            "sz003",
            "sz300",
            "sz301",
            # sz302 是深市新开的段：2026-10-03 用新浪节点交叉比对时发现 tdx
            # 旧段表漏了它（sz302132 只在新浪侧出现，段表枚举不到就是永远扫不出
            # 这个代码）。新增的段要留在这儿，别等它变成一只"同步不到"的孤股。
            "sz302",
        ),
        index_bit=False,
    ),
    AssetClass(
        name="bse",
        label="北交所（bj*，tdx 协议层拒，只能从全 A 节点里筛）",
        # 新浪没有 bse 独立节点（实测 ``bjs`` 空），但 ``hs_a`` 里含 bj 前缀。
        sina_node="hs_a",
        # 只用 bj920：实测（新浪 hs_a 全量 + tdx 段探测）北交所代码现在全落在
        # 920 段，430/830/870 这些旧段一个都没有。段表留空的段会让每轮 --scan
        # 白探几千个注定空的候选。
        tdx_segments=("bj920",),
        index_bit=False,
    ),
    AssetClass(
        name="etf",
        label="交易所 ETF（沪 5xxxxx / 深 1xxxxx）",
        sina_node=None,
        tdx_segments=(
            "sh510",
            "sh511",
            "sh512",
            "sh513",
            "sh515",
            "sh516",
            "sh519",
            "sh520",
            "sh530",
            "sh560",
            "sh561",
            "sh562",
            "sh563",
            "sh588",
            "sh589",
            "sz158",
            "sz159",
        ),
        index_bit=False,
    ),
    AssetClass(
        name="lof",
        label="LOF 上市开放式基金（sh50xxxx / sz16xxxx）",
        sina_node=None,
        tdx_segments=("sh501", "sh502", "sz160", "sz161", "sz163", "sz164", "sz165"),
        index_bit=False,
    ),
    AssetClass(
        name="bond",
        label="可转债 / 可交换债（sh110-118 / sz121-131）",
        sina_node=None,
        tdx_segments=("sh110", "sh111", "sh113", "sh118", "sz121", "sz123", "sz127", "sz131"),
        index_bit=False,
    ),
    AssetClass(
        name="index",
        label="指数（sh000* / sz399*，走 0x052D 指数位）",
        sina_node=None,
        tdx_segments=("sh000", "sz399"),
        index_bit=True,
    ),
    AssetClass(
        name="bshare",
        label="B 股（沪 sh900* / 深 sz200*）",
        sina_node="hs_b",
        tdx_segments=("sh900", "sz200"),
        index_bit=False,
    ),
)

_BY_NAME: Final[dict[str, AssetClass]] = {item.name: item for item in ASSET_CLASSES}


def _nodes_to_classes() -> dict[str, tuple[str, ...]]:
    """``新浪节点 → 供应它的类别们``（多值：``hs_a`` 同时供 stock 与 bse）。

    从 :data:`ASSET_CLASSES` **推导**而不是手抄一份：手抄的表会在"给某个类别
    换了节点"之后悄悄变旧，而这张表的唯一用途就是"从一个节点返回的一整包里
    该拆出哪几类"——它错了，拆类别就会错。
    """
    mapping: dict[str, list[str]] = {}
    for item in ASSET_CLASSES:
        if item.sina_node:
            mapping.setdefault(item.sina_node, []).append(item.name)
    return {node: tuple(names) for node, names in mapping.items()}


#: 新浪节点 → 该节点供应的全部类别（由 :data:`ASSET_CLASSES` 推导）。
NODE_TO_CLASSES: Final[dict[str, tuple[str, ...]]] = _nodes_to_classes()

#: 已实测未接线的取数源（真机校准后再登记到 :class:`AssetClass`）。
#: 写这里而不是写进 :class:`AssetClass`，是为了让"缺口"在文档里可见，
#: 而不是悄悄变成一份过期的猜测表。
TBD_SOURCES: Final[tuple[tuple[str, str], ...]] = (
    (
        "eastmoney_clist",
        "ETF/LOF/可转债/指数的 fs 映射（b:MK0021 / b:MK0354 等）需真机校准；"
        "本轮实测 push2 全站 IP 级软限流（Server disconnected），暂不登记",
    ),
)


def asset_class_names() -> tuple[str, ...]:
    """全部类别名（展示/CLI 用）。"""
    return tuple(item.name for item in ASSET_CLASSES)


def class_of(name: str) -> AssetClass:
    """按名字取类别定义；未知类别抛 ``KeyError``（别静默当 stock）。"""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(f"未知标的类别 {name!r}；可选：{', '.join(asset_class_names())}") from None


def classify(symbol: str) -> str:
    """按代码前缀判定类别（与 sync 脚本同口径）。

    >>> classify("sh600519")
    'stock'
    >>> classify("sh000001")
    'index'
    >>> classify("bj920000")
    'bse'
    """
    token = symbol.strip().lower()
    # ``bj`` 单独判前缀、不放进段表：北交所开新段（430/830 → 920）时这里是
    # "仍然是 bse"，放进段表就是"新段悄悄变成 stock"。判据必须是
    # ``startswith("bj")``——早先写成 ``token[:3] == "bj"``，对真实代码恒为假
    # （``"bj920000"[:3]`` 是 ``"bj9"``），于是这条保护只剩 ``bj`` 两个字面量
    # 能触发，bse 实际全靠段表里的 ``bj920`` 兜着。
    # B 股则相反——它的段（``sh900`` / ``sz200``）是稳定的，走下面的段表匹配就够，
    # 不必再抄一份前缀判据（早先那句 ``token[2:4] in ("00", "01")`` 永远为假）。
    if token.startswith("bj"):
        return "bse"
    # 按类别表里的段顺序匹配（后面的段不会抢走前面的，index/sh000 仍判 index）
    for item in ASSET_CLASSES:
        if token.startswith(item.tdx_segments):
            return item.name
    return "stock"


def classify_with_index(symbol: str) -> tuple[str, bool]:
    """类别 + 该类别是否走 0x052D 指数位。"""
    kind = classify(symbol)
    return kind, kind == "index"


def class_of_directory(name: str) -> str:
    """类别 → 落盘目录（目前同名，保留这层是为将来分目录改名留口子）。"""
    return _DIR_BY_KIND.get(name, name)


#: 新浪节点的中文名（仅展示）。
SINA_NODES: Final[tuple[tuple[str, str], ...]] = (
    ("hs_a", "沪深 A 股（含北交所 bj*）"),
    ("sh_a", "沪 A"),
    ("sz_a", "深 A"),
    ("cyb", "创业板"),
    ("kcb", "科创板"),
    ("hs_b", "B 股（sh900* / sz200*）"),
    ("hs_etf", "ETF（实测为空，不可用）"),
    ("bond_hq", "可转债（实测为空，不可用）"),
    ("index", "指数（实测为空，不可用）"),
)
