# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""7709 标准族公共常量与请求体构造（K 线类别 / 市场编号 / 0x0530 请求体）。

P11-3 自 ``std7709.py`` 拆出（REFACTOR_PLAN_v11），内容逐字搬移。
"""

from __future__ import annotations

from ...errors import ParseError

# K 线周期

# --------------------------------------------------------------------------- #


class KlineCategory:
    """K 线周期类别（请求参数 ``category``）。"""

    MIN_5 = 0

    MIN_15 = 1

    MIN_30 = 2

    HOUR_1 = 3

    DAY = 4

    WEEK = 5

    MONTH = 6

    MIN_1 = 7

    MIN_1_ALT = 8

    DAY_ALT = 9

    SEASON = 10

    YEAR = 11

    NAMES = {
        0: "5min",
        1: "15min",
        2: "30min",
        3: "1hour",
        4: "day",
        5: "week",
        6: "month",
        7: "1min",
        8: "1min",
        9: "day",
        10: "season",
        11: "year",
    }

    @classmethod
    def name_of(cls, category: int) -> str:

        return cls.NAMES.get(category, f"category_{category}")


#: 日线及以上：datetime 为 **uint32 YYYYMMDD**

DAYLIKE_CATEGORIES = frozenset({4, 5, 6, 9, 10, 11})

#: 分钟线：datetime 为 **uint16 lc16 日期 + uint16 当日分钟数**

MINUTELIKE_CATEGORIES = frozenset({0, 1, 2, 3, 7, 8})


#: 成交量以「手」下发的周期。

#:

#: ✅ 实测（3 台主站 × 3 个标的完全一致）：``category`` 属于本集合时，

#: 服务端把**成交量按「手」**下发，而成交额仍是「元」。直接相除会得到

#: 约 100 倍于股价的均价，极易被误判为"数据错误"。

#:

#: 判据：同一标的同一交易日，``week`` 的成交额与 ``day`` **完全相同**，

#: 成交量恰好是 1/100；把周/月的量按 100 倍还原后，``amount / volume``

#: 落回 OHLC 区间，且日均量与日线量级吻合。

#:

#: 注意 ``9``（日线-alt）也在此列——它与 ``4``（日线）**单位不同**，

#: 这是 TDX 的历史遗留，不要想当然地按"日线"归组。

VOLUME_LOT_CATEGORIES = frozenset({5, 6, 9, 10, 11})

#: 1 手 = 100 股

SHARES_PER_LOT = 100


# --------------------------------------------------------------------------- #

# 市场编码

# --------------------------------------------------------------------------- #


class Market:
    """7709 标准族的市场编号。"""

    SZ = 0  # 深交所（000 / 002 / 300 / 15x / 16x / 18x）

    SH = 1  # 上交所（600 / 601 / 603 / 605 / 688 / 5xx / 11x）

    #: 北交所（v5 DC1：0x044E 实测 market=2 → 379，独立市场编号；
    #: 此前与深市共用 0 导致北交所数据全链路缺失）

    BJ = 2

    #: 深市代码前缀（用于从代码反推市场）

    SZ_PREFIXES: tuple[str, ...] = (
        "001",
        "002",
        "003",
        "004",
        "300",
        "301",
        "150",
        "159",
        "160",
        "161",
        "164",
        "165",
        "166",
        "167",
        "168",
        "169",
        "180",
        "184",
        "399",
    )

    #: 沪市代码前缀

    SH_PREFIXES: tuple[str, ...] = (
        "500",
        "510",
        "511",
        "512",
        "513",
        "515",
        "518",
        "588",
        "600",
        "601",
        "603",
        "605",
        "688",
        "689",
        "110",
        "113",
        "132",
    )

    #: 北交所代码前缀（v5 DC1，与 domain/symbol.py 单一事实源一致：

    #: 北交所段优先于沪深判定，避免 ``4``/``8`` 系被兜底吞入深市）

    BJ_PREFIXES: tuple[str, ...] = (
        "43",
        "83",
        "87",
        "88",
        "92",
        "4",
        "8",
    )


def infer_market(code: str) -> int:
    """从证券代码反推标准市场编号（0=深 1=沪）。



    采用**最长前缀匹配**。特殊处理 ``000xxx``：该段同时覆盖

    沪市指数（``000001`` 上证指数、``000002`` A 股指数……）与

    深市主板个股（``000651`` 格力电器、``000858`` 五粮液），

    按惯例**后三位 < 100 视为沪市指数，其余归深市个股**。



    >>> infer_market("600000"), infer_market("000651"), infer_market("300750")

    (1, 0, 0)

    >>> infer_market("000001")

    1



    .. note::

       ``000001`` 在两市都存在（沪=上证指数，深=平安银行），本函数按

       指数惯例取沪市。若要取深市个股，请显式传入 ``market`` 参数。

    """

    code = (code or "").strip()

    if not code:
        raise ParseError("证券代码为空，无法推断市场", context={"code": code})

    if code.startswith("000") and code[3:].isdigit() and int(code[3:]) < 100:
        return Market.SH

    # 北交所段优先判定（v5 DC1：43/83/87/88/92/4/8 开头 → market=2），
    # 必须先于深/沪前缀与兜底，否则 4/8 开头会被「其余归深」吞掉。

    for pfx in Market.BJ_PREFIXES:
        if code.startswith(pfx):
            return Market.BJ

    for pfx in Market.SZ_PREFIXES:
        if code.startswith(pfx):
            return Market.SZ

    for pfx in Market.SH_PREFIXES:
        if code.startswith(pfx):
            return Market.SH

    # 兜底：6/5/1 开头归沪，其余归深

    return Market.SH if code[0] in "651" else Market.SZ


def quote_request_market(std_market: int) -> int:
    """把「标准市场编号」换算为 **0x0530 请求体**使用的市场字节。

    .. danger::

       ✅ 实测：**0x0530 的 market 语义与其余命令相反**——

       深市代码必须用请求字节 ``1``，沪市必须用 ``0``。写反时服务端不会报错，

       而是返回一个**帧结构合法但内容错误**的响应（实测固定回声 ``600839``

       且载荷全零）。这是典型的「错答案 + 合法帧」脏数据，

       因此 :class:`RealtimeQuoteParser` 强制做回声校验。

    v5 DC1：北交所（标准编号 2）的请求字节**未真机定标**，直传 ``2``——

    二元反转 ``1 - std`` 在 market=2 时会产出 ``-1``，``bytes()`` 直接

    ValueError 崩溃；直传 2 若与服务端约定不符，回声校验兜底拦截

    （IntegrityViolation 明确报错，绝不产生静默脏数据）。

    >>> quote_request_market(Market.SZ), quote_request_market(Market.SH)

    (1, 0)

    >>> quote_request_market(Market.BJ)

    2

    """

    m = int(std_market)

    if m in (Market.SZ, Market.SH):
        return 1 - m

    if m == Market.BJ:
        return Market.BJ  # 未定标：回声校验兜底

    raise ValueError(
        f"0x0530 请求市场字节未定义 std_market={m}（可选 0=深 1=沪 2=北）",
        # context 由调用方 TdxError 体系补足；此处 ValueError 保持纯函数语义
    )


def build_realtime_quote_body(code: str, market: int | None = None) -> bytes:
    """构造 0x0530 的 8 字节请求体。



    :param code: 6 位证券代码

    :param market: 标准市场编号；省略时由 :func:`infer_market` 推断



    >>> build_realtime_quote_body("600000").hex()

    '0100363030303030'

    >>> build_realtime_quote_body("000651").hex()

    '0101303030363531'

    """

    if market is None:
        market = infer_market(code)

    raw = code.encode("ascii", errors="replace")[:6].ljust(6, b"\x00")

    if len(raw) != 6:
        raise ParseError(f"证券代码必须为 6 位: {code!r}", context={"code": code})

    return bytes([0x01, quote_request_market(market)]) + raw


# --------------------------------------------------------------------------- #
