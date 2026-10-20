# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""标的类别 x K 线周期 矩阵（0x052D 的横截面覆盖）。

前几轮修的都是**单点**缺陷：指数位、日期值域、成交量单位。单点测试再多也
回答不了另一个问题——**换一个标的、换一个周期，这套规则还成立吗**？

本文件把矩阵摊开：

* 段表全部 **54 个代码段**（7 类：stock 13 / bse 1 / etf 17 / lof 7 / bond 8 /
  index 2 / bshare 2）；
* 两个周期族：日线族（``day``/``week``，代表 category 4/5）与分钟族
  （``5min``，代表 category 0）——族的编码形状（日期族、量单位族）由族决定，
  族内各编号共享同一条解析路径，所以族代表即族全体；
* 每一格都走真实门面调用链 ``TdxClient.bars`` → 分页 → dispatch → 解码，
  断言请求体编码、日期值域、成交量单位三件事。

矩阵里**实际不成立**的格子必须显式钉住而不是漏掉：

1. **北交所**（market=2）在 symbol-based 7709 请求上未验证，`split_symbol`
   直接 fail-closed（`ParseError` E3040）。`tests/client/
   test_bj_symbol_protocol_boundary.py` 钉了裸码 ``430047``，这里补带前缀写法、
   补其余旧段，并把「**规划期放行、执行期失败**」这个分野钉死——调用方必须
   能预判这个失败发生在哪一层。
2. **段表之外的代码**兜底成 ``stock``：请求照常发出，不会被拦。这是既有设计
   （`classify` 的兜底分支），但静默兜底意味着一个不存在的代码会被当成股票
   去问主站，所以把它钉成显式事实而不是让它在某次重构里悄悄消失。

E 节再覆盖另一个协议族的同一横截面——``0x0530`` 实时行情的标的维度：

3. **北交所 quotes 与 bars 的失败形态不同**：``bars`` 抛 ``ParseError``，而
   ``quotes`` 走「坏标的隔离」——把同一道闸拦下的错误收进 ``last_errors`` /
   ``_collect`` 并返回空。这不矛盾（批量语义需要隔离），但**单标的调用方只看
   返回值会以为"没有数据"而不是"协议层未验证"**，所以两种形态都要钉住。
4. **可转债 quotes 会被回声校验拦下**：真实主站对 bond 的 market 字节不认，回一个
   56 字节、回声 ``600839``、载荷非零的**合法帧**——这是「错答案 + 合法帧」，
   光看帧长和结构完全检不出来，只能靠校验回声代码。钉住这条能防的是解析器某天
   被改成"信任服务端回声"。
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from atst.client.core import _PERIOD_TO_CATEGORY, _PREFIX_MARKET, period_to_category
from atst.client.sync import TdxClient
from atst.codec.framing import ResponseFrame
from atst.codec.primitive import decode_tdx_float, encode_leb128
from atst.diagnostics import WarningCode, warning_sink
from atst.domain.integrity import date_problem
from atst.domain.period import CANONICAL_PERIODS, PERIOD_ALIASES
from atst.errors import IntegrityViolation, ParseError, ValidationError
from atst.protocol.parsers._std7709_common import (
    DAYLIKE_CATEGORIES,
    MINUTELIKE_CATEGORIES,
    SHARES_PER_LOT,
    VOLUME_LOT_CATEGORIES,
)
from atst.universe import ASSET_CLASSES, classify, classify_with_index

pytestmark = pytest.mark.unit

_MAGIC = 0x47444954

#: 成交量 / 成交额的 tdx_float 种子（与 test_index_bars_layout 同一对，取 2 的整次幂
#: 让单位换算的期望值可手算）。
VOLUME_SEED = 0x4D000000
AMOUNT_SEED = 0x4F000000
VOLUME_RAW = decode_tdx_float(VOLUME_SEED)

#: 价格差分：open / close / high / low（基准 0 起）。
PRICE_DIFFS = (30000, 10, 20, -5)

#: 三条日线记录的日期与指数尾部（涨跌家数）。
DATES = (20261013, 20261014, 20261015)
TAILS = ((1200, 800), (1201, 801), (1202, 802))

#: 三个周期族代表：daylike 里的「按股」格（category 4）、「按手」格（5），
#: 以及 minutelike（0）。
PERIOD_KIND = {
    "day": 4,
    "week": 5,
    "5min": 0,
}


def _record(category: int, i: int, *, tail: tuple[int, int] | None) -> bytes:
    """一条 K 线记录：日期族不同、量字段与差分一致。"""
    if category in MINUTELIKE_CATEGORIES:
        # lc16 编码：year = raw // 2048 + 2004，month/day 取余。
        raw_date = (2026 - 2004) * 2048 + 10 * 100 + 13 + i
        out = struct.pack("<HH", raw_date, 9 * 60 + 30)
    else:
        out = struct.pack("<I", DATES[i])
    out += b"".join(encode_leb128(v) for v in PRICE_DIFFS)
    out += struct.pack("<II", VOLUME_SEED, AMOUNT_SEED)
    if tail is not None:
        out += struct.pack("<HH", *tail)
    return out


class _MatrixPool:
    """按请求体里的 category 回对应形状的载荷，并按标的回带指数尾部。

    必须真的按请求体里的市场位 / 代码回数据——如果回错了标的的载荷，
    矩阵断言就会把「池答非所问」误当成「库解析正确」。
    """

    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001
        mkt, code, cat = struct.unpack("<H6sH", body[0:10])
        self.bodies.append(body)
        symbol = ("sz" if mkt == 0 else "sh") + code.rstrip(b"\x00").decode("ascii")
        index = classify_with_index(symbol)[1]
        records = [_record(cat, i, tail=TAILS[i] if index else None) for i in range(len(TAILS))]
        payload = struct.pack("<H", len(records)) + b"".join(records)
        return ResponseFrame(
            magic=_MAGIC,
            zip_flag=0,
            seq=1,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=payload,
        )


def _bars(symbol: str, *, period: str, count: int = 3) -> tuple[Any, list[bytes]]:
    """走真实门面调用链，返回（行, 请求体列表）。"""
    pool = _MatrixPool()
    client = TdxClient(pool=pool)  # type: ignore[arg-type]
    with warning_sink() as caveats:
        rows = client.bars(symbol, period=period, count=count)
    return rows, pool.bodies, caveats


def _category_of(body: bytes) -> int:
    return struct.unpack("<H", body[8:10])[0]


# --------------------------------------------------------------------------- #
# A. 段表 SSOT：54 段的分类必须自洽
# --------------------------------------------------------------------------- #


def _segments() -> list[tuple[str, str]]:
    """``(代码段, 它属于的类别名)``——测试的完整横截面分母。"""
    out: list[tuple[str, str]] = []
    for item in ASSET_CLASSES:
        out.extend((segment, item.name) for segment in item.tdx_segments)
    return out


_SEGMENTS: tuple[tuple[str, str], ...] = tuple(_segments())

#: 端到端矩阵的分母：排除 bse。北交所 market=2 在 symbol-based 7709 请求上未验证，
#: 请求会在发帧**之前**被拦下，所以它不进"解码得干净"这张矩阵，而是进 C 节
#: "必须 fail-closed"的名单（另见 ``tests/client/test_bj_symbol_protocol_boundary.py``）。
#: 两个名单合起来才是完整分母，少一侧就会把缺口漏成"没人测过"。
_BARS_SEGMENTS: tuple[tuple[str, str], ...] = tuple(
    (segment, asset) for segment, asset in _SEGMENTS if asset != "bse"
)


def test_bars_matrix_and_fail_closed_matrix_partition_the_segment_table() -> None:
    """两张名单互斥且覆盖全表：段表改了，这里会指名哪一段无主。"""
    bars = {segment for segment, _a in _BARS_SEGMENTS}
    fail_closed = {segment for segment, asset in _SEGMENTS if asset == "bse"}
    all_segments = {segment for segment, _a in _SEGMENTS}
    assert bars | fail_closed == all_segments
    assert bars & fail_closed == set()


def test_segment_table_covers_all_classes() -> None:
    """段的总数就是矩阵的分母——段表改了，这里先响。"""
    assert len(_SEGMENTS) == sum(len(c.tdx_segments) for c in ASSET_CLASSES)
    assert all(item.tdx_segments for item in ASSET_CLASSES)


def test_every_segment_classifies_as_the_class_that_declared_it() -> None:
    for segment, expected in _SEGMENTS:
        assert classify(f"{segment}001") == expected, (
            f"段 {segment!r} 由 {expected} 声明，但 classify 判成 {classify(f'{segment}001')!r}"
        )


def test_segments_do_not_overlap_between_classes() -> None:
    """一个段只能属于一类：重叠会让 classify 按表顺序抢答，后者永远不可达。"""
    for segment, owner in _SEGMENTS:
        for other in ASSET_CLASSES:
            if other.name == owner:
                continue
            if segment.startswith(other.tdx_segments):
                pytest.fail(f"段 {segment!r} 同时落在 {owner} 与 {other.name}")


def test_only_the_index_class_carries_the_index_bit() -> None:
    """指数位是指数类独有的——其余六类走错就是记录流错位。"""
    for item in ASSET_CLASSES:
        assert item.index_bit is (item.name == "index"), item.name


def test_every_class_has_a_distinct_directory() -> None:
    directories = [item.directory for item in ASSET_CLASSES]
    assert len(set(directories)) == len(directories), directories


# --------------------------------------------------------------------------- #
# B. 端到端矩阵：每一格都必须干净
# --------------------------------------------------------------------------- #


def _expected_volume(category: int, *, index: bool) -> int:
    """类别规则与指数规则的**复合**乘数，照解析器顺序乘，不合并。

    两个乘数独立生效，所以指数周线是 ``100 x 100 = 10000``：LOT 族的服务端原值
    已经是「手」（×100），指数的日线族原值在类别规则之外**还要再小 100 倍**（×100）。
    合并成一个数会丢掉这条复合语义，也看不出是哪一层贡献的。
    """
    factor = SHARES_PER_LOT if category in VOLUME_LOT_CATEGORIES else 1
    if index and category in DAYLIKE_CATEGORIES:
        factor *= SHARES_PER_LOT
    return int(round(VOLUME_RAW * factor))


def test_index_volume_multiplier_is_the_product_of_both_rules() -> None:
    """指数 + LOT 族 = x10000：两个规则叠加，不是取其一。"""
    rows, _bodies, caveats = _bars("sh000001", period="week")
    assert rows[0]["volume"] == int(round(VOLUME_RAW * SHARES_PER_LOT * SHARES_PER_LOT))
    assert caveats == []


@pytest.mark.parametrize("segment, asset", _BARS_SEGMENTS)
@pytest.mark.parametrize("period", ["day", "week", "5min"], ids=lambda p: p)
@pytest.mark.filterwarnings("ignore::UserWarning")  # 指数分钟族刻意走到名义值告警，断言已校验内容
def test_every_segment_every_family_decodes_clean(segment: str, asset: str, period: str) -> None:
    """段 x 周期族 的全矩阵：请求体编码正确、日期在真日历内、告警为空。"""
    symbol = f"{segment}001"
    kind, index = classify_with_index(symbol)
    rows, bodies, caveats = _bars(symbol, period=period)

    assert kind == asset
    assert index is (asset == "index")
    assert len(rows) == 3
    assert len(bodies) == 1

    mkt, code, cat = struct.unpack("<H6sH", bodies[0][0:10])
    assert cat == PERIOD_KIND[period] == period_to_category(period)
    assert code.rstrip(b"\x00").decode("ascii") == symbol[2:]
    assert mkt == _PREFIX_MARKET[symbol[:2]]

    for row in rows:
        assert date_problem(row["datetime"]) is None, row["datetime"]
        assert row["volume"] == _expected_volume(cat, index=index), row

    codes = {c.code for c in caveats}
    if index and cat in MINUTELIKE_CATEGORIES:
        # 指数分钟线的量是名义值，唯一的期望告警就是这条；多一条少一条都算回归。
        assert codes == {WarningCode.INDEX_MINUTE_VOLUME_NOMINAL}, codes
    else:
        assert caveats == [], f"{symbol} {period} 不该带告警：{[c.message for c in caveats]}"


@pytest.mark.filterwarnings("ignore::UserWarning")  # 本测试就是冲着这条告警来的
def test_index_minute_family_warns_its_volume_is_nominal() -> None:
    """指数分钟线的量是名义值（恒等于成交额/100），不乘，必须告警。"""
    rows, _bodies, caveats = _bars("sh000001", period="5min")
    assert rows[0]["volume"] == int(round(VOLUME_RAW))
    codes = {c.code for c in caveats}
    assert WarningCode.INDEX_MINUTE_VOLUME_NOMINAL in codes


@pytest.mark.parametrize("period", ["day", "week", "5min"], ids=lambda p: p)
def test_non_index_symbol_never_warns_nominal_volume(period: str) -> None:
    """负控：非指数标的任何周期族都不该报名义值告警。"""
    _rows, _bodies, caveats = _bars("sh600519", period=period)
    assert WarningCode.INDEX_MINUTE_VOLUME_NOMINAL not in {c.code for c in caveats}


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("5min", int(round(VOLUME_RAW))),
        ("day", int(round(VOLUME_RAW))),
        ("week", int(round(VOLUME_RAW * 100))),
    ],
    ids=lambda p: p,
)
def test_volume_unit_depends_on_the_family_not_the_symbol(period: str, expected: int) -> None:
    """单位是**流**的属性（周期族决定），不是这条标的的属性。"""
    rows, _bodies, _caveats = _bars("sh600519", period=period)
    assert rows[0]["volume"] == expected


# --------------------------------------------------------------------------- #
# C. market 位与 fail-closed
# --------------------------------------------------------------------------- #


def test_sh_prefixes_request_market_one_and_sz_zero() -> None:
    """市场位是前缀的纯函数，54 段里除北交所外逐段验证。"""
    for segment, _asset in _BARS_SEGMENTS:
        symbol = f"{segment}001"
        _rows, bodies, _caveats = _bars(symbol, period="day")
        mkt = struct.unpack("<H", bodies[0][0:2])[0]
        assert mkt == _PREFIX_MARKET[symbol[:2]], symbol


@pytest.mark.parametrize("symbol", ["bj920001", "bj430047", "bj830799", "bj899050"])
def test_unverified_bj_market_fails_before_transport(symbol: str) -> None:
    """带前缀写法与旧段一起钉：市场位未验证必须在发帧之前拦下。"""

    class _NoIo:
        def request(self, *_args, **_kwargs):
            raise AssertionError("未验证的北交所请求不得走到传输层")

    client = TdxClient(pool=_NoIo())  # type: ignore[arg-type]
    with pytest.raises(ParseError, match="当前只允许 SZ/SH"):
        client.bars(symbol, period="day", count=3)


def test_bj_failure_cannot_be_made_legal_by_an_explicit_market_override() -> None:
    """显式传 ``market=2`` 不是绕过手段——同一道闸。"""
    client = TdxClient(pool=_MatrixPool())  # type: ignore[arg-type]
    with pytest.raises(ParseError, match="当前只允许 SZ/SH"):
        client.bars("bj920001", period="day", count=3, market=2)


def test_plan_stage_accepts_bj_but_execution_rejects_it() -> None:
    """分野必须显式：北交所在规划期看起来合法，执行期才失败。

    调用方要靠这个区分来给出准确报错（"该类标的协议层未验证"而不是"网络故障"），
    所以两个阶段的行为都要钉住，任何一侧变向都算回归。
    """
    from atst.query import QueryPlanner, QuerySpec

    plan = QueryPlanner().compile(
        QuerySpec.build("bars", symbols="bj920001", period="day", count=3)
    )
    assert plan is not None

    client = TdxClient(pool=_MatrixPool())  # type: ignore[arg-type]
    with pytest.raises(ParseError, match="market=2"):
        client.bars("bj920001", period="day", count=3)


@pytest.mark.parametrize("symbol", ["sh604000", "sz100000", "sh500000", "sh999999"])
def test_unlisted_segment_falls_back_to_stock_explicitly(symbol: str) -> None:
    """段表之外的代码兜底成 stock 并照常发请求——这是既有设计，钉成显式事实。

    不拦它（主站会回空页，调用方看得见"没有数据"），但兜底口径必须写在
    测试里：某次重构若把兜底改成抛错，这里会响，需要连带改文档与 CLI。
    """
    assert classify(symbol) == "stock"
    rows, bodies, caveats = _bars(symbol, period="day")
    assert len(bodies) == 1
    assert caveats == []
    assert len(rows) == 3


# --------------------------------------------------------------------------- #
# D. 周期拼写的别名面：别名必须与规范拼写落到同一根 category
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("alias", "canonical"),
    sorted(PERIOD_ALIASES.items()),
    ids=lambda value: value,
)
def test_period_alias_resolves_to_the_same_category_as_its_target(
    alias: str, canonical: str
) -> None:
    """拼写别名只换写法、不换语义：两根必须落到同一个 category 编号。"""
    assert period_to_category(alias) == period_to_category(canonical)
    assert _PERIOD_TO_CATEGORY[alias] == _PERIOD_TO_CATEGORY[canonical]


@pytest.mark.parametrize("empty", ["", " "])
def test_empty_period_is_rejected(empty: str) -> None:
    with pytest.raises(ValidationError, match="非空字符串"):
        period_to_category(empty)


@pytest.mark.parametrize("bad", ["1tick", "abc", "13min", "dayly", "0x4"])
def test_unknown_period_spelling_is_rejected_not_silently_rewritten(bad: str) -> None:
    """拼错不许静默改写成别的东西——那是取错了周期还以为取对了。"""
    with pytest.raises(ValidationError, match="未知"):
        period_to_category(bad)


@pytest.mark.parametrize(("loose", "expected"), [("D", 4), ("DAY ", 4), (" 1M ", 7), ("WEEKLY", 5)])
def test_period_input_is_stripped_and_lowercased(loose: str, expected: int) -> None:
    """入口做了 strip + lower 归一化——这条是既有宽容行为，写下来免得被顺手收紧。"""
    assert period_to_category(loose) == expected


@pytest.mark.parametrize("raw", ["12", "99"])
def test_out_of_range_category_is_rejected(raw: str) -> None:
    """编号在协议集合里之外的（0..11 之外）必须拒，而不是当未知拼写糊过去。"""
    with pytest.raises(ValidationError, match="category"):
        period_to_category(raw)


@pytest.mark.parametrize("raw", ["9"])
def test_category_with_no_canonical_spelling_is_still_addressable(raw: str) -> None:
    """category 9（日线的「按手」变体）没有规范拼写别名，但按编号仍可寻址。"""
    assert period_to_category(raw) == 9


def test_tick_has_no_bar_category() -> None:
    """``tick`` 是规范周期但没有 K 线编号（分笔走另一条命令），不许伪造。"""
    assert "tick" in CANONICAL_PERIODS
    assert "tick" not in _PERIOD_TO_CATEGORY
    with pytest.raises(ValidationError):
        period_to_category("tick")


# --------------------------------------------------------------------------- #
# E. 0x0530 实时行情的标的维度
# --------------------------------------------------------------------------- #

#: 0x0530 实时行情的 market 字节与其余命令**相反**（深=1 沪=0），由
#: ``build_realtime_quote_body`` 处理；这里只需知道请求体形状：market(u8) + code(6s)。
_PRICE = 130000  # 1300.00 元（price_scale=100）


def _quote_payload(market: int, code: str) -> bytes:
    """一条最小可用的 0x0530 响应。

    ``sentinel`` 恒等于 ``-price``，那是解析器的自校验位，写错会额外发一条漂移告警。
    末尾的 ``tdx_float`` 是成交额——它落在价格×成交量区间外会产生一条解码告警，
    测试不断言成交额，所以不追平它（追平会让载荷看起来"太干净"而失去代表性）。
    """
    out = struct.pack("<B", market) + code.encode("ascii")
    out += b"\xcc\x08"  # offset 7..8 不透明区
    for value in (_PRICE, 10, -20, 60, -90, 1, -_PRICE, 16000, 3, 0):
        out += encode_leb128(value)
    out += struct.pack("<I", AMOUNT_SEED)
    return out


class _QuotePool:
    """按请求体里的 (market, code) 回对应的行情帧——回错标的就是自欺。

    请求体前两字节是 ``[0x01, request_market]``——**request_market 已按 0x0530 的
    反转语义换算**，而响应回声是**标准**市场编号（sh=1 / sz=0）。

    若按 ``<H6s`` 把前两字节合并读成一个 ``uint16``，flag 会左移 8 位混进 market：
    沪市 request_market=0 恰好算出 1 **蒙对**，深市 request_market=1 算出 0x0101=257
    于是错。这类巧合最容易让人以为测试是对的，所以这里显式拆开两字节。
    """

    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001
        assert body[0] == 0x01, body
        request_market = body[1]
        code = body[2:8].rstrip(b"\x00").decode("ascii")
        self.bodies.append(body)
        return ResponseFrame(
            magic=_MAGIC,
            zip_flag=0,
            seq=1,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=_quote_payload(1 - request_market, code),
        )


@pytest.mark.parametrize(("symbol", "request_market"), [("sh600519", 0), ("sz000001", 1)])
def test_quotes_market_byte_is_reversed_only_in_the_request(
    symbol: str, request_market: int
) -> None:
    """0x0530 的 market 反转只发生在**请求体**，响应回声是标准编号。

    反转由 ``quote_request_market()`` 承担（请求体写 ``1 - 标准编号``），
    解析器则拿**标准编号**去校回声——两侧必须同时成立，任一侧写错就落成
    IntegrityViolation。真机抓包确认 sh600519 的回声字节是 0x01。

    沪深两侧都必须进矩阵：只用沪市标的会得到巧合式全绿（见 ``_QuotePool`` 注释）。
    """
    pool = _QuotePool()
    rows = TdxClient(pool=pool).quotes([symbol])  # type: ignore[arg-type]

    body = pool.bodies[0]
    assert body[:2] == bytes([0x01, request_market])
    assert body[2:8] == symbol[2:].encode("ascii")
    assert [row["code"] for row in rows] == [symbol[2:]]


def test_quotes_echo_market_mismatch_is_rejected() -> None:
    """回声市场与请求不符同样必须拦下：那是"错答案 + 合法帧"的市场维度变体。"""
    pool = _QuotePool()
    # 请求 sh600519（标准编号 1），却回深市 0
    pool.request = lambda cmd, body, timeout=None: ResponseFrame(  # type: ignore[method-assign]
        magic=_MAGIC,
        zip_flag=0,
        seq=1,
        method=cmd,
        zip_size=0,
        unzip_size=0,
        payload=_quote_payload(0, "600519"),
    )

    client = TdxClient(pool=pool)  # type: ignore[arg-type]
    with pytest.raises(IntegrityViolation, match="回声市场不匹配"):
        client.quotes(["sh600519"])


@pytest.mark.parametrize("symbol", ["bj920001", "bj430047", "bj830799", "bj899050"])
def test_quotes_on_unverified_bj_market_is_isolated_not_raised(symbol: str) -> None:
    """北交所 quotes 不抛错，而是被隔离——返回空、错误进 ``_collect``。

    ``bars`` 对同一个标的抛 ``ParseError``，两条能力失败形态不同：quotes 的批量
    语义要求坏标的只能淘汰自己。这里钉的是「隔离之后**错误必须留痕**」，
    而不是「不许返回空」——留痕被丢才是真缺陷。
    """

    class _NoIo:
        def request(self, *_args, **_kwargs):
            raise AssertionError("未验证的北交所请求不得走到传输层")

    client = TdxClient(pool=_NoIo())  # type: ignore[arg-type]
    collected: list[tuple[str, BaseException]] = []
    rows = client.quotes([symbol], _collect=collected)

    assert rows == []
    assert len(collected) == 1
    assert collected[0][0] == symbol
    assert "当前只允许 SZ/SH" in str(collected[0][1])


def test_quotes_echo_mismatch_is_rejected_not_accepted() -> None:
    """回声不匹配必须拦下：这是「错答案 + 合法帧」，帧长与结构完全正常。

    真实主站对 bond 的 market 字节不认，会回一个 56 字节、回声 ``600839`` 的
    合法帧（可转债 quotes 实测 5 个标的全部返回同一个 600839）。解析器靠校验
    回声代码识别它——这条钉的是**不信任服务端回声**这个原则。

    注意它走的是**直接抛出**而不是进 ``_collect``：解析发生在 try 之外，见下方
    那条测试。
    """
    pool = _QuotePool()
    pool.request = lambda cmd, body, timeout=None: ResponseFrame(  # type: ignore[method-assign]
        magic=_MAGIC,
        zip_flag=0,
        seq=1,
        method=cmd,
        zip_size=0,
        unzip_size=0,
        payload=_quote_payload(1, "600839"),
    )

    client = TdxClient(pool=pool)  # type: ignore[arg-type]
    with pytest.raises(IntegrityViolation, match="回声代码不匹配"):
        client.quotes(["sh110031"])


def test_quotes_empty_payload_is_rejected_as_a_placeholder() -> None:
    """回声对了、载荷全零：这是占位响应，不许当成"行情为 0"解码出来。

    必须让回声匹配才测得到这一层——全零载荷会先在回声校验处被拦下（code 读成
    空串），所以只清零 offset 7 之后的字段区。
    """
    pool = _QuotePool()
    pool.request = (
        lambda cmd, body, timeout=None: ResponseFrame(  # type: ignore[method-assign]
            magic=_MAGIC,
            zip_flag=0,
            seq=1,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=_quote_payload(1, "600519")[:7] + b"\x00" * 49,
        )
    )

    client = TdxClient(pool=pool)  # type: ignore[arg-type]
    with pytest.raises(IntegrityViolation, match="空载荷"):
        client.quotes(["sh600519"])


def test_single_batch_quotes_isolates_request_building_but_not_parsing() -> None:
    """隔离的边界必须清楚：只覆盖「请求体构造」失败，不覆盖「解析」失败。

    构造期失败（如北交所 market 未验证）只淘汰那只标的、错误进 ``_collect``；
    解析期失败（回声不匹配、空载荷）发生在 try 之外，**直接抛出并中断整批**。
    这不是矛盾——但调用方若以为"批量=全部隔离"就会漏处理整批失败，所以两侧
    的行为都钉在这里。并发版 ``quotes_concurrent`` 在外层多包了一层 ``except
    Exception``，同一批混坏标的在那边是能隔离的，两侧语义不同是既有事实。
    """
    pool = _QuotePool()
    client = TdxClient(pool=pool)  # type: ignore[arg-type]

    collected: list[tuple[str, BaseException]] = []
    rows = client.quotes(["sh600519", "bj920001"], _collect=collected)
    assert [row["code"] for row in rows] == ["600519"]
    assert [sym for sym, _e in collected] == ["bj920001"]

    pool.request = lambda cmd, body, timeout=None: ResponseFrame(  # type: ignore[method-assign]
        magic=_MAGIC,
        zip_flag=0,
        seq=1,
        method=cmd,
        zip_size=0,
        unzip_size=0,
        payload=_quote_payload(1, "600839"),
    )
    with pytest.raises(IntegrityViolation):
        client.quotes(["sh600519"])
