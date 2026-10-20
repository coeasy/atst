# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""0x052D 指数 K 线的记录布局与成交量单位（2026-10-13 实测回归的固化）。

指数 K 线每条记录尾部多 4 字节（上涨家数 + 下跌家数），股票没有。这个位不开，
解码器就不消费那 4 字节，于是**下一条**记录的 ``datetime`` 从尾巴上开始读——
整条记录流从此错位。错位还是混沌的（LEB128 变长，偶尔会自己对齐回来），实测
``sh000001`` 取 3 根时"第 1、3 根全错、第 2 根恰好对"，比整页全错更难发现。

本文件用自建载荷钉住三件事：

1. 指数位开 → 记录流对齐、缓冲区按字节精确耗尽、涨跌家数在场；
2. 指数位关 → 日期落进值域尺子的网里（与 ``tests/domain/test_integrity.py`` 联动）；
3. 成交量单位：指数日线族的原值在既有类别规则之外**还要再小 100 倍**；
   指数分钟线的原值是**名义值**（恒等于成交额/100），不乘，只告警。

golden 库（``tests/golden/quotation/``，530 份实采）里 0x052D 覆盖 14 组
market/code/category 组合，**全是股票**：market 0 的 ``000001``/``300750``、
market 1 的 ``600000``。指数是 market 1 的 ``000001``——同一串 6 位码，只是市场
前缀不同，而 golden 库恰好只有前者。这也解释了缺陷为何能躺这么久：同名样本在
库里的每一次重放都通过了。所以这里的载荷是自建的，``tdx_float`` 的期望值由
:func:`decode_tdx_float` 反算，而不是另写一份编码器：编码规则若与解码规则不对称，
测试会跟着一起错。
"""

from __future__ import annotations

import struct
import warnings

import pytest

from atst.client.sync import TdxClient
from atst.codec.framing import ResponseFrame
from atst.codec.primitive import BinaryReader, decode_tdx_float, encode_leb128
from atst.diagnostics import WarningCode, warning_sink
from atst.domain.integrity import date_problem
from atst.protocol.parsers._std7709_bars import SecurityBarsParser
from atst.protocol.parsers._std7709_common import (
    DAYLIKE_CATEGORIES,
    MINUTELIKE_CATEGORIES,
    VOLUME_LOT_CATEGORIES,
)

pytestmark = pytest.mark.unit

#: 成交量 / 成交额的 tdx_float 载荷。刻意选 2 的整次幂，让单位换算的期望值是手算得出的。
#: 期望值由解码器自己反算，并在导入时断言一次——解码规则若变，这里先响。
VOLUME_SEED = 0x4D000000
AMOUNT_SEED = 0x4F000000
VOLUME_RAW = decode_tdx_float(VOLUME_SEED)
AMOUNT_RAW = decode_tdx_float(AMOUNT_SEED)

#: 三条记录的日期与涨跌家数。
DATES = (20261013, 20261014, 20261015)
EXPECTED_DATES = ("2026-10-13", "2026-10-14", "2026-10-15")
TAILS = ((1200, 800), (1201, 801), (1202, 802))

#: 价格差分（基准 0 起）：open_diff / close_diff / high_diff / low_diff。
PRICE_DIFFS = (30000, 10, 20, -5)


def _day_record(
    date_int: int,
    *,
    price_diffs: tuple[int, int, int, int] = PRICE_DIFFS,
    tail: tuple[int, int] | None = None,
) -> bytes:
    out = struct.pack("<I", date_int)
    out += b"".join(encode_leb128(v) for v in price_diffs)
    out += struct.pack("<I", VOLUME_SEED) + struct.pack("<I", AMOUNT_SEED)
    if tail is not None:
        out += struct.pack("<HH", *tail)
    return out


def _minute_record(
    raw_date: int,
    minutes: int,
    *,
    tail: tuple[int, int] | None = None,
) -> bytes:
    out = struct.pack("<HH", raw_date, minutes)
    out += b"".join(encode_leb128(v) for v in PRICE_DIFFS)
    out += struct.pack("<I", VOLUME_SEED) + struct.pack("<I", AMOUNT_SEED)
    if tail is not None:
        out += struct.pack("<HH", *tail)
    return out


def _payload(records: list[bytes]) -> bytes:
    return struct.pack("<H", len(records)) + b"".join(records)


def _index_day_payload(n: int = 3) -> bytes:
    return _payload(
        [_day_record(DATES[i], tail=TAILS[i]) for i in range(n)],
    )


def _stock_day_payload(n: int = 2) -> bytes:
    return _payload([_day_record(DATES[i]) for i in range(n)])


def _parse(payload: bytes, *, category: int, index: bool, **ctx: object) -> list[dict]:
    reader = BinaryReader(payload)
    sink: list[str] = []
    rows = SecurityBarsParser().parse_payload(
        reader, category=category, index=index, _warnings=sink, **ctx
    )
    assert reader.remaining >= 0
    return rows


# --------------------------------------------------------------------------- #
# 1. 指数位：开则对齐，关则混沌
# --------------------------------------------------------------------------- #


def test_tdx_float_seeds_decode_to_exact_powers_of_two() -> None:
    """自建载荷的前提：期望值是手算得出的，不靠猜。"""
    assert VOLUME_RAW == 134217728.0
    assert AMOUNT_RAW == 2147483648.0


def test_index_bit_on_aligns_the_record_stream() -> None:
    rows = _parse(_index_day_payload(), category=4, index=True)
    assert [r["date"] for r in rows] == list(EXPECTED_DATES)
    assert [r["up_count"] for r in rows] == [t[0] for t in TAILS]
    assert [r["down_count"] for r in rows] == [t[1] for t in TAILS]


def test_index_bit_on_exhausts_the_buffer_exactly() -> None:
    """「缓冲区按字节精确耗尽」是布局判据的一部分：多读少读都说明步长错了。"""
    reader = BinaryReader(_index_day_payload())
    SecurityBarsParser().parse_payload(reader, category=4, index=True, _warnings=[])
    assert reader.remaining == 0


def test_index_bit_off_desyncs_the_stream() -> None:
    """指数位不开 → 混沌错位：日期落进值域尺子的网，涨跌家数消失，少乘 100。"""
    rows = _parse(_index_day_payload(), category=4, index=False)
    dates = [r["date"] for r in rows]
    assert dates != list(EXPECTED_DATES), "错位后不可能三条日期全对"
    assert any(date_problem(d) is not None for d in dates), (
        f"错位日期必须全部能被尺子认出，实际 {dates}"
    )
    assert all("up_count" not in r for r in rows)
    assert rows[0]["volume"] == int(round(VOLUME_RAW))


def test_index_bit_off_loses_the_first_volume_scaling() -> None:
    """第 1 条日期往往还是对的（基准恰好对齐），但成交量已经少乘 100。"""
    rows = _parse(_index_day_payload(n=1), category=4, index=False)
    assert rows[0]["date"] == EXPECTED_DATES[0]
    assert rows[0]["volume"] == int(round(VOLUME_RAW))
    assert "up_count" not in rows[0]


def test_stock_bit_matches_stock_payload() -> None:
    rows = _parse(_stock_day_payload(), category=4, index=False)
    assert [r["date"] for r in rows] == ["2026-10-13", "2026-10-14"]
    assert all("up_count" not in r for r in rows)
    # cat 4 不在 VOLUME_LOT_CATEGORIES：原值即「股」。
    assert rows[0]["volume"] == int(round(VOLUME_RAW))


def test_index_bit_does_not_eat_into_nothing() -> None:
    """``remaining >= 4`` 守卫：只有一条股票记录、尾部没有字节时不该吞掉任何东西。

    注意成交量**照样**按指数口径 ×100——单位是流的属性，不是"这条有没有尾巴"的属性。
    """
    rows = _parse(_stock_day_payload(n=1), category=4, index=True)
    assert rows[0]["date"] == EXPECTED_DATES[0]
    assert "up_count" not in rows[0]
    assert rows[0]["volume"] == int(round(VOLUME_RAW * 100))


# --------------------------------------------------------------------------- #
# 2. 成交量单位：指数日线族在类别规则之外再小 100 倍
# --------------------------------------------------------------------------- #


def _daylike_volume_factor(*, index: bool, category: int) -> int:
    factor = 100 if category in VOLUME_LOT_CATEGORIES else 1
    return factor * 100 if index else factor


@pytest.mark.parametrize("category", sorted(DAYLIKE_CATEGORIES))
def test_index_daylike_volume_is_hundred_times_smaller_on_the_wire(category: int) -> None:
    rows = _parse(_index_day_payload(n=1), category=category, index=True)
    assert rows[0]["volume"] == int(
        round(VOLUME_RAW * _daylike_volume_factor(index=True, category=category))
    )


@pytest.mark.parametrize("category", sorted(DAYLIKE_CATEGORIES))
def test_stock_daylike_volume_keeps_the_category_rule(category: int) -> None:
    rows = _parse(_stock_day_payload(n=1), category=category, index=False)
    assert rows[0]["volume"] == int(
        round(VOLUME_RAW * _daylike_volume_factor(index=False, category=category))
    )


@pytest.mark.parametrize("category", sorted(DAYLIKE_CATEGORIES))
def test_volume_unit_share_disables_every_conversion(category: int) -> None:
    """``volume_unit="share"`` 声明"服务端原值就是股"：指数位与类别规则都不该再插手。"""
    rows = _parse(_index_day_payload(n=1), category=category, index=True, volume_unit="share")
    assert rows[0]["volume"] == int(round(VOLUME_RAW))


@pytest.mark.parametrize("category", sorted(DAYLIKE_CATEGORIES))
def test_volume_unit_lot_forces_one_hundred(category: int) -> None:
    rows = _parse(_stock_day_payload(n=1), category=category, index=False, volume_unit="lot")
    assert rows[0]["volume"] == int(round(VOLUME_RAW * 100))


def test_unknown_volume_unit_fails_closed() -> None:
    from atst.errors import ParseError

    with pytest.raises(ParseError):
        _parse(_stock_day_payload(n=1), category=4, index=False, volume_unit="lots")


# --------------------------------------------------------------------------- #
# 3. 指数分钟线：名义成交量，不乘，只告警
# --------------------------------------------------------------------------- #


def _minute_payload() -> bytes:
    raw_date = (2026 - 2004) * 2048 + 10 * 100 + 13
    return _payload([_minute_record(raw_date, 10 * 60, tail=TAILS[0])])


@pytest.mark.parametrize("category", sorted(MINUTELIKE_CATEGORIES))
def test_index_minute_volume_is_nominal_and_warns(category: int) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rows = _parse(_minute_payload(), category=category, index=True)
    assert rows[0]["volume"] == int(round(VOLUME_RAW)), "名义值原样保留，不乘 100"
    assert any("index_minute_volume_nominal" in str(w.message) for w in caught)
    assert any(f"category={category}" in str(w.message) for w in caught)


@pytest.mark.parametrize("category", sorted(MINUTELIKE_CATEGORIES))
def test_stock_minute_volume_is_not_warned(category: int) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rows = _parse(_minute_payload(), category=category, index=False)
    assert not any("index_minute_volume_nominal" in str(w.message) for w in caught)
    assert rows[0]["volume"] == int(round(VOLUME_RAW))


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_index_minute_record_still_aligns() -> None:
    """分钟线的 datetime 是 uint16+uint16，指数位同样要开。"""
    rows = _parse(_minute_payload(), category=0, index=True)
    assert rows[0]["date"] == "2026-10-13"
    assert rows[0]["time"] == "10:00"
    assert (rows[0]["up_count"], rows[0]["down_count"]) == TAILS[0]


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_amount_is_never_rescaled() -> None:
    """成交额无论股票指数、日线分钟，都是「元」——单位换算只发生在 volume 上。"""
    for index in (True, False):
        rows = _parse(_index_day_payload(n=1), category=4, index=index)
        assert rows[0]["amount"] == AMOUNT_RAW
        rows = _parse(_minute_payload(), category=0, index=index)
        assert rows[0]["amount"] == AMOUNT_RAW


def test_price_differentials_are_independent_of_the_index_bit() -> None:
    """价格腿不受指数位影响：错位只发生在记录边界上。"""
    clean = _parse(_index_day_payload(n=1), category=4, index=True)[0]
    assert (clean["open"], clean["close"]) == (30.0, 30.01)
    assert (clean["high"], clean["low"]) == (30.02, 29.995)


# --------------------------------------------------------------------------- #
# 4. 端到端：公网上取指数日线的完整回路
# --------------------------------------------------------------------------- #


_MAGIC = 0x0074CBB1


class _FakePool:
    """按命令号回固定载荷；未预置的命令直接报错（不许测试悄悄走别的面）。"""

    def __init__(self, table: dict[int, bytes]) -> None:
        self.table = table
        self.bodies: list[bytes] = []

    def request(self, cmd: int, body: bytes, timeout=None):  # noqa: ANN001
        if cmd not in self.table:
            raise AssertionError(f"未预置命令 {cmd:#06x}")
        self.bodies.append(body)
        return ResponseFrame(
            magic=_MAGIC,
            zip_flag=0,
            seq=1,
            method=cmd,
            zip_size=0,
            unzip_size=0,
            payload=self.table[cmd],
        )


@pytest.mark.parametrize("symbol", ["sh000001", "sz399001"])
def test_public_bars_on_an_index_is_clean_out_of_the_box(symbol: str) -> None:
    """原始症状的正面修复：主入口取指数日线不需要调用方知道有任何"指数位"。

    2026-10-13 之前 ``Client().bars("sh000001")`` 拿到的是 ``10112-28-10`` 这类日期
    加 1e40 的成交量；现在同一条调用必须干净返回，且一条越域告警都不许带。
    这条钉的是 ``TdxClient.bars`` 一层——门面 :meth:`Client.bars` 与执行器各自推断过，
    唯独模板层把缺省写死成 ``False``，直连调用照样错位。
    """
    pool = _FakePool({0x052D: _index_day_payload()})
    client = TdxClient(pool=pool)  # type: ignore[arg-type]
    with warning_sink() as caveats:
        rows = client.bars(symbol, period="day", count=3)
    assert [r["date"] for r in rows] == list(EXPECTED_DATES)
    assert [r["up_count"] for r in rows] == [t[0] for t in TAILS]
    assert caveats == [], f"干净结果不该带任何告警：{[c.message for c in caveats]}"
    body = pool.bodies[-1]
    mkt = 0 if symbol.startswith("sz") else 1
    assert body[0:2] == struct.pack("<H", mkt)
    assert body[2:8] == symbol[2:].encode("ascii")
    assert body[8:10] == struct.pack("<H", 4), "day 的 category 必须是 4"


def test_public_bars_async_side_matches_sync() -> None:
    """异步侧同一判据：同步/异步共享同一个 ``_t_bars`` 模板，推断不得只在一侧生效。"""
    import asyncio

    from atst.client.async_ import AsyncTdxClient

    class _AsyncPool(_FakePool):
        async def request(self, cmd, body, timeout=None):  # noqa: ANN001
            return _FakePool.request(self, cmd, body, timeout)

    client = AsyncTdxClient(pool=_AsyncPool({0x052D: _index_day_payload()}))  # type: ignore[arg-type]
    with warning_sink() as caveats:
        rows = asyncio.run(client.bars("sh000001", period="day", count=3))
    assert [r["date"] for r in rows] == list(EXPECTED_DATES)
    assert [r["up_count"] for r in rows] == [t[0] for t in TAILS]
    assert caveats == []


@pytest.mark.filterwarnings(
    "ignore::UserWarning"
)  # 本测试就是冲着这条告警来的，别让 pytest 再报一遍
def test_public_bars_on_an_index_does_not_warn_where_it_once_did_not() -> None:
    """原始症状的另一半：告警必须**必然**响，而不是"有时响有时不响"。

    旧尺子锚定 4 位年份，于是同一批错位数据里 ``sh000300`` 响、``sh000001`` 不响。
    现在显式关掉指数位（唯一还会踩这个坑的入口）必须每次都能被 wire 上的告警拦住。
    """
    client = TdxClient(pool=_FakePool({0x052D: _index_day_payload()}))  # type: ignore[arg-type]
    with warning_sink() as caveats:
        rows = client.bars("sh000001", period="day", count=3, index=False)
    dates = [r["date"] for r in rows]
    assert any(date_problem(d) is not None for d in dates), dates
    offenders = [c for c in caveats if c.code is WarningCode.FIELD_OUT_OF_DOMAIN]
    assert offenders, f"错位日期上 wire 却没告警：{[c.message for c in caveats]}"
    assert "row[" in offenders[0].message, offenders[0].message


def test_public_bars_on_a_stock_is_untouched_by_the_fix() -> None:
    """负控：修指数不能顺手改动股票路径——同一条命令、同一份解析器。"""
    client = TdxClient(pool=_FakePool({0x052D: _stock_day_payload()}))  # type: ignore[arg-type]
    with warning_sink() as caveats:
        rows = client.bars("sh600519", period="day", count=2)
    assert [r["date"] for r in rows] == ["2026-10-13", "2026-10-14"]
    assert rows[0]["volume"] == int(round(VOLUME_RAW))
    assert all("up_count" not in r for r in rows)
    assert caveats == []
