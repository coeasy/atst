# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""7709 标准族公共常量与请求体构造（K 线类别 / 市场编号 / 0x0530 请求体）。

市场身份只允许由 :mod:`tstdx.domain.symbol` 的 canonical symbol engine 推断；
本模块不得再维护第二套 ``000xxx``/北交所启发式。
"""

from __future__ import annotations

from ...errors import ParseError


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
#: 约 100 倍于股价的均价。注意 9（日线-alt）与 4（日线）单位不同。
VOLUME_LOT_CATEGORIES = frozenset({5, 6, 9, 10, 11})
SHARES_PER_LOT = 100


class Market:
    """7709 标准族的市场编号。"""

    SZ = 0
    SH = 1
    #: 北交所（0x044E 实测 market=2 → 379）。
    BJ = 2

    # 兼容公开常量：推断逻辑不再消费这些表，市场判定统一委托 domain.symbol。
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
    BJ_PREFIXES: tuple[str, ...] = ("43", "83", "87", "88", "92", "4", "8")


def _require_six_digit_code(code: str) -> str:
    if not isinstance(code, str):
        raise ParseError(
            f"证券代码必须是 6 位数字字符串，收到 {type(code).__name__}",
            context={"code": repr(code)},
        )
    normalized = code.strip()
    if len(normalized) != 6 or not normalized.isascii() or not normalized.isdigit():
        raise ParseError(
            f"证券代码必须是 6 位 ASCII 数字: {code!r}",
            context={"code": code},
        )
    return normalized


def infer_market(code: str) -> int:
    """按全库 canonical symbol engine 推断市场编号（0=深 1=沪 2=北）。

    ``000xxx`` 歧义不再由协议层自建启发式裁决。例如裸 ``000001`` 与
    :func:`tstdx.domain.symbol.to_tdx_market` 一致归深市平安银行；若调用方要
    上证指数，必须显式提供 ``market=1`` 或使用 ``sh000001`` 在更高层解析。
    """

    normalized = _require_six_digit_code(code)
    from ...domain.symbol import to_tdx_market

    market, canonical_code = to_tdx_market(normalized)
    if canonical_code != normalized or market not in (Market.SZ, Market.SH, Market.BJ):
        raise ParseError(
            f"证券代码无法映射到 TDX A/BJ 市场: {code!r}",
            context={"code": code, "market": market},
        )
    return market


def quote_request_market(std_market: int) -> int:
    """把标准市场编号换算为 **0x0530 请求体** market 字节。

    ✅ 实测：深市 0 → 请求字节 1；沪市 1 → 请求字节 0。北交所标准编号
    2 的 0x0530 请求字节尚未真机定标，当前保持 2 直传，并依赖
    :class:`RealtimeQuoteParser` 的 code/market 回声校验阻断脏数据。
    """

    if isinstance(std_market, bool) or not isinstance(std_market, int):
        raise ParseError(
            f"0x0530 标准 market 必须是整数 0|1|2，收到 {std_market!r}",
            context={"market": std_market},
        )
    if std_market in (Market.SZ, Market.SH):
        return 1 - std_market
    if std_market == Market.BJ:
        return Market.BJ
    raise ParseError(
        f"0x0530 请求市场字节未定义 std_market={std_market}（可选 0=深 1=沪 2=北）",
        context={"market": std_market},
    )


def build_realtime_quote_body(code: str, market: int | None = None) -> bytes:
    """构造 0x0530 的 8 字节请求体。

    ``code`` 必须是 6 位 ASCII 数字；省略 ``market`` 时只通过统一 symbol
    SSOT 推断。显式 market 也必须是严格整数 0/1/2，不做字符串/浮点/bool
    强制转换。
    """

    normalized = _require_six_digit_code(code)
    std_market = infer_market(normalized) if market is None else market
    request_market = quote_request_market(std_market)
    return bytes([0x01, request_market]) + normalized.encode("ascii")
