"""F2/F4：7727 扩展市场 + GOODS 商品 + MAC 板块解析器布局锁定回归。

这些协议族全部为**洁净室推断**（未 golden 回归，见 ``std7727.py`` /
``goods.py`` / ``mac.py`` 模块 docstring）。本文件用合成载荷把当前布局
固化为回归基线（与 ``TestP1cDiffBase`` 锁定 0x0104 同机制），防止实现被
"顺手对齐"其他命令而静默翻转。真机 golden 样本到位后，与样本一起翻转
对应断言。

同步覆盖两类：
1. **解析器布局**：0x0100–0x010E（EXTENDED）、0x0200–0x020A（GOODS）、
   0x120F/0x1210/0x2000（MAC 板块）逐命令合成载荷回归；
2. **client 方法接线**：新暴露的 ``ex_*`` / ``goods_*`` / ``block_*``
   方法经 FakePool 验证「命令号 + family → 正确解析器」通路。
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.codec.primitive import encode_leb128
from tstdx.protocol.commands import Family
from tstdx.protocol.registry import dispatch

pytestmark = pytest.mark.unit

MAGIC = 0x0074CBB1


def _frame(method: int, payload: bytes, family: str = Family.STANDARD) -> ResponseFrame:
    return ResponseFrame(
        magic=MAGIC,
        zip_flag=0,
        seq=0,
        method=method,
        zip_size=len(payload),
        unzip_size=len(payload),
        payload=payload,
    )


def _rows(method: int, payload: bytes, family: str, **ctx) -> list[dict]:
    result = dispatch(_frame(method, payload, family), family=family, **ctx)
    assert not any("L1 解析失败" in w for w in result.warnings), result.warnings
    return result.rows


def _quote_payload(code: bytes = b"007000", market: int = 0) -> bytes:
    """单条扩展市场报价合成载荷（布局见 ExInstrumentQuoteParser）。

    ``<B market><6s code><2B 不透明区><9×leb128><4B tdx_float>``。
    """
    p = struct.pack("<B", market) + code + b"\x00\x00"
    # 字段序：price, d_last, d_open, d_high, d_low, u4, sentinel, volume, v2
    for v in (100, 10, 20, 30, 5, 1, 0, 1000, 0):
        p += encode_leb128(v)
    p += b"\x00\x00\x00\x00"  # tdx_float amount = 0.0
    return p


# --------------------------------------------------------------------------- #
# EXTENDED 0x0100–0x010E
# --------------------------------------------------------------------------- #
class TestExExtendedLayout:
    FAMILY = Family.EXTENDED

    def test_0100_market_count(self):
        rows = _rows(0x0100, struct.pack("<H", 5), self.FAMILY)
        assert rows == [{"count": 5}]

    def test_0101_market_list(self):
        rec = struct.pack("<H", 0) + "深市".encode("gbk").ljust(8, b"\x00")
        rec += struct.pack("<H", 1) + "港股".encode("gbk").ljust(8, b"\x00")
        rows = _rows(0x0101, struct.pack("<H", 2) + rec, self.FAMILY)
        assert rows == [{"market_id": 0, "name": "深市"}, {"market_id": 1, "name": "港股"}]

    def test_0102_instrument_count(self):
        rows = _rows(0x0102, struct.pack("<H", 3), self.FAMILY)
        assert rows == [{"count": 3}]

    def test_0103_instrument_list(self):
        rec = struct.pack("<H", 0) + b"00700\x00" + "腾讯控股".encode("gbk").ljust(8, b"\x00")
        rec += struct.pack("<H", 0) + b"09988\x00" + "阿里巴巴".encode("gbk").ljust(8, b"\x00")
        rows = _rows(0x0103, struct.pack("<H", 2) + rec, self.FAMILY)
        assert rows[0]["code"] == "00700"
        assert rows[0]["name"] == "腾讯控股"
        assert rows[1]["code"] == "09988"
        assert len(rows) == 2

    def test_0104_instrument_bars_with_oi(self):
        # 28B 记录：<I dt> + 4×leb128 + 2×tdx_float + <I oi>（P1c per-record absolute）
        rec = (
            struct.pack("<I", 20260102)
            + encode_leb128(100)
            + encode_leb128(10)
            + encode_leb128(20)
            + encode_leb128(5)
            + struct.pack("<II", 0, 0)
            + struct.pack("<I", 12345)
            + b"\x00" * 7  # 补齐 28B，满足 guarded_count(28) 的逐条守卫
        )
        rows = _rows(0x0104, struct.pack("<H", 1) + rec, self.FAMILY, category=4)
        assert rows[0]["datetime"] == "20260102"
        assert rows[0]["open"] == pytest.approx(100 / 1000, abs=1e-4)
        assert rows[0]["close"] == pytest.approx(110 / 1000, abs=1e-4)
        assert rows[0]["high"] == pytest.approx(120 / 1000, abs=1e-4)
        assert rows[0]["low"] == pytest.approx(105 / 1000, abs=1e-4)
        assert rows[0]["open_interest"] == 12345

    def test_0105_instrument_quote(self):
        rows = _rows(0x0105, _quote_payload(), self.FAMILY)
        row = rows[0]
        assert row["code"] == "007000"
        assert row["price"] == pytest.approx(1.0, abs=1e-4)
        assert row["last_close"] == pytest.approx(1.1, abs=1e-4)
        assert row["open"] == pytest.approx(1.2, abs=1e-4)
        assert row["high"] == pytest.approx(1.3, abs=1e-4)
        assert row["low"] == pytest.approx(1.05, abs=1e-4)
        assert row["volume"] == 100000  # leb128 1000 × 100 手→股
        assert row["amount"] == pytest.approx(0.0, abs=1e-4)

    def test_0106_instrument_trade(self):
        rec = (
            struct.pack("<H", 535) + struct.pack("<f", 7.51) + b"\x00\x00\x00\x00" + b"\x01"
        )  # 11B：<H 秒><f4 价><tdx_float 量><B 方向>
        rows = _rows(0x0106, struct.pack("<H", 2) + rec * 2, self.FAMILY)
        assert len(rows) == 2
        assert rows[0]["second"] == 535
        assert rows[0]["price"] == pytest.approx(7.51, abs=1e-4)
        assert rows[1]["direction"] == 1

    def test_0107_instrument_minute(self):
        rec = (
            struct.pack("<H", 1 * 60 + 30)
            + struct.pack("<f", 7.51)
            + struct.pack("<f", 7.52)
            + b"\x00\x00\x00\x00"
        )  # 14B
        rows = _rows(0x0107, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["time"] == "01:30"
        assert rows[0]["price"] == pytest.approx(7.51, abs=1e-4)
        assert rows[0]["avg_price"] == pytest.approx(7.52, abs=1e-4)

    def test_0108_instrument_info(self):
        rec = b"00700\x00" + "腾讯控股".encode("gbk").ljust(8, b"\x00")
        rec += struct.pack("<ff", 100.0, 0.01) + b"\x00" * 12  # 32B
        rows = _rows(0x0108, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["code"] == "00700"
        assert rows[0]["name"] == "腾讯控股"
        assert rows[0]["multiplier"] == pytest.approx(100.0, abs=1e-4)
        assert rows[0]["tick"] == pytest.approx(0.01, abs=1e-4)

    def test_0109_batch_quote_known_single_record_limitation(self):
        """0x0109 已知洁净室局限：单条报价尾部贪婪消费 → 多记录只解析首条。

        该行为是**有意的现状锁定**（见 ExBatchQuoteParser docstring）——
        尾部（五档）布局未 golden 锁定，不可臆造记录长度。真机样本到位后
        翻转本条。
        """
        payload = struct.pack("<H", 2) + _quote_payload(b"007000") + _quote_payload(b"007001")
        result = dispatch(_frame(0x0109, payload, self.FAMILY), family=self.FAMILY)
        assert len(result.rows) == 1
        assert result.rows[0]["code"] == "007000"
        assert any("记录截断" in w for w in result.warnings), result.warnings

    @pytest.mark.parametrize(
        "cmd,parser", [(0x010A, "ExHkQuoteParser"), (0x010B, "ExUsQuoteParser")]
    )
    def test_010a_010b_market_quotes_share_layout(self, cmd, parser):
        result = dispatch(_frame(cmd, _quote_payload(), self.FAMILY), family=self.FAMILY)
        assert result.meta["parser"] == parser
        assert result.rows[0]["code"] == "007000"

    @pytest.mark.parametrize(
        "cmd,parser",
        [
            (0x010C, "ExFutureQuoteParser"),
            (0x010D, "ExFxQuoteParser"),
            (0x010E, "ExOptionQuoteParser"),
        ],
    )
    def test_010c_010e_market_quotes_share_layout(self, cmd, parser):
        result = dispatch(_frame(cmd, _quote_payload(), self.FAMILY), family=self.FAMILY)
        assert result.meta["parser"] == parser
        assert result.rows[0]["code"] == "007000"


# --------------------------------------------------------------------------- #
# GOODS 0x0200–0x020A
# --------------------------------------------------------------------------- #
class TestGoodsLayout:
    FAMILY = Family.GOODS

    def test_0200_count(self):
        rows = _rows(0x0200, struct.pack("<H", 6), self.FAMILY)
        assert rows == [{"count": 6}]

    def test_0201_list(self):
        rec = b"rb0000" + "螺纹钢".encode("gbk").ljust(8, b"\x00") + struct.pack("<H", 1)
        rows = _rows(0x0201, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["code"] == "rb0000"
        assert rows[0]["name"] == "螺纹钢"
        assert rows[0]["category"] == 1

    def test_0202_bars_with_oi_running_base(self):
        # 0x0202 与 0x052D 同构：跨记录 running base。
        # 记录为**天然恰好 28B**（4×leb128 合计 12B + 2×tdx_float + <I oi>）：
        # 解析器按 leb128 变长字段连续消费、不跳 padding，逐条补齐会破坏对齐。
        def rec(open_diff: int, close_diff: int, high_diff: int, low_diff: int) -> bytes:
            return (
                struct.pack("<I", 20260102)
                + encode_leb128(open_diff)
                + encode_leb128(close_diff)
                + encode_leb128(high_diff)
                + encode_leb128(low_diff)
                + struct.pack("<II", 0, 0)
                + struct.pack("<I", 777)
            )

        r1 = rec(10000, 10000, 20000, 20000)  # leb128 3+3+3+3=12B
        r2 = rec(0, 10000, 2000000, 2000000)  # leb128 1+3+4+4=12B
        assert len(r1) == 28 and len(r2) == 28, "记录须恰好 28B 满足 guarded_count(28)"

        rows = _rows(0x0202, struct.pack("<H", 2) + r1 + r2, self.FAMILY, category=4)
        assert rows[0]["open"] == pytest.approx(10000 / 1000, abs=1e-4)
        assert rows[0]["close"] == pytest.approx(20000 / 1000, abs=1e-4)
        assert rows[0]["open_interest"] == 777
        # 第二条 open_diff=0 → running base 累加为 rec1 close（与 0x0104 per-record 不同）
        assert rows[1]["open"] == pytest.approx(20000 / 1000, abs=1e-4)
        assert rows[1]["close"] == pytest.approx(30000 / 1000, abs=1e-4)
        assert rows[1]["open_interest"] == 777

    def test_0203_quote(self):
        rows = _rows(0x0203, _quote_payload(), self.FAMILY)
        assert rows[0]["price"] == pytest.approx(1.0, abs=1e-4)
        assert "extra" in rows[0]

    def test_0204_trade(self):
        rec = struct.pack("<H", 535) + struct.pack("<f", 7.51) + b"\x00\x00\x00\x00" + b"\x01"
        rows = _rows(0x0204, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["second"] == 535
        assert rows[0]["direction"] == 1

    def test_0205_minute(self):
        rec = (
            struct.pack("<H", 9 * 60 + 15)
            + struct.pack("<f", 7.51)
            + struct.pack("<f", 7.52)
            + b"\x00\x00\x00\x00"
        )
        rows = _rows(0x0205, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["time"] == "09:15"

    def test_0206_info(self):
        # 名称 GBK ≤ 8B，记录补齐整 32B（RECORD_SIZE）
        rec = b"rb2610" + "螺纹钢".encode("gbk").ljust(8, b"\x00")
        rec += struct.pack("<ff", 10.0, 1.0) + b"\x00" * 10
        rows = _rows(0x0206, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["multiplier"] == pytest.approx(10.0, abs=1e-4)

    def test_0207_holding(self):
        rec = b"rb2610" + b"\x00\x00\x00\x00"
        rows = _rows(0x0207, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["code"] == "rb2610"
        assert rows[0]["open_interest"] == 0

    def test_0208_option_greeks(self):
        rec = b"cu2600" + struct.pack("<fffff", 0.5, 0.2, 0.1, 0.05, 0.01)
        rows = _rows(0x0208, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["delta"] == pytest.approx(0.5, abs=1e-6)
        assert rows[0]["rho"] == pytest.approx(0.01, abs=1e-6)

    def test_0209_fx_rate(self):
        # 名称 GBK ≤ 8B，保证汇率字段从偏移 14 起（26B = RECORD_SIZE）
        rec = b"USDCNY" + "美元".encode("gbk").ljust(8, b"\x00")
        rec += struct.pack("<fff", 7.1, 7.05, 7.15)
        rows = _rows(0x0209, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["bid_cash"] == pytest.approx(7.1, abs=1e-6)
        assert rows[0]["ask"] == pytest.approx(7.15, abs=1e-6)

    def test_020a_calendar(self):
        rec = b"rb2610" + struct.pack("<I", 20261015)
        rows = _rows(0x020A, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["expire_date"] == "20261015"


# --------------------------------------------------------------------------- #
# MAC 板块 0x120F / 0x1210 / 0x2000（F2 板块数据在线化核心）
# --------------------------------------------------------------------------- #
class TestMacBlockLayout:
    FAMILY = Family.MAC

    def test_120f_block_list(self):
        rec = "白酒概念".encode("gbk").ljust(8, b"\x00") + struct.pack("<H", 1)
        rec += "新能源".encode("gbk").ljust(8, b"\x00") + struct.pack("<H", 2)
        rows = _rows(0x120F, struct.pack("<H", 2) + rec, self.FAMILY)
        assert rows == [{"name": "白酒概念", "block_id": 1}, {"name": "新能源", "block_id": 2}]

    def test_1210_block_members(self):
        rows = _rows(0x1210, struct.pack("<H", 2) + b"600519" + b"000858", self.FAMILY)
        assert rows == [{"code": "600519"}, {"code": "000858"}]

    def test_2000_block_quote(self):
        rec = b"600519" + struct.pack("<ff", 1.25, 1297.5)
        rows = _rows(0x2000, struct.pack("<H", 1) + rec, self.FAMILY)
        assert rows[0]["code"] == "600519"
        assert rows[0]["pct_change"] == pytest.approx(1.25, abs=1e-4)
        assert rows[0]["price"] == pytest.approx(1297.5, abs=1e-4)


# --------------------------------------------------------------------------- #
# client 方法接线（FakePool → 命令号 + family → 解析器）
# --------------------------------------------------------------------------- #
class _FakePool:
    """返回指定命令号的合成帧；记录最后一次请求的命令号与 body。"""

    def __init__(self, payload_builder) -> None:
        self._builder = payload_builder
        self.last_cmd: int | None = None
        self.last_body: bytes | None = None

    def request(self, cmd: int, body: bytes, timeout: float = 5.0) -> ResponseFrame:
        self.last_cmd = cmd
        self.last_body = body
        return _frame(cmd, self._builder(cmd, body), Family.STANDARD)


class _AsyncFakePool(_FakePool):
    async def request(self, cmd: int, body: bytes, timeout: float = 5.0) -> ResponseFrame:
        return super().request(cmd, body, timeout)


def _payload_for(cmd: int, body: bytes) -> bytes:
    """按命令号返回能通过其解析器的最小合成载荷（仅用于接线测试）。"""
    if cmd in (0x0100, 0x0102, 0x0200):
        return struct.pack("<H", 1)
    if cmd == 0x0101:
        # EX_MARKET_LIST：<H count> + <H market_id><8s name>
        rec = struct.pack("<H", 1) + "测试市场".encode("gbk").ljust(8, b"\x00")
        return struct.pack("<H", 1) + rec
    if cmd == 0x120F:
        # MAC_BLOCK_LIST：<H count> + <8s name><H block_id>
        rec = "测试板块".encode("gbk").ljust(8, b"\x00") + struct.pack("<H", 1)
        return struct.pack("<H", 1) + rec
    if cmd == 0x0103:
        rec = struct.pack("<H", 0) + b"00700\x00" + "腾讯控股".encode("gbk").ljust(8, b"\x00")
        return struct.pack("<H", 1) + rec
    if cmd == 0x0201:
        rec = b"rb0000" + "螺纹钢".encode("gbk").ljust(8, b"\x00") + struct.pack("<H", 1)
        return struct.pack("<H", 1) + rec
    if cmd == 0x1210:
        return struct.pack("<H", 1) + b"600519"
    return b"\x00\x00"


@pytest.mark.unit
class TestClientWiring:
    """新增 client 方法 → 命令号/family → 解析器 通路（不触网）。"""

    def test_mac_block_list_wiring(self):
        from tstdx.client import MacClient

        pool = _FakePool(_payload_for)
        client = MacClient(pool=pool)
        rows = client.block_list(block_type=0, start=0)
        assert pool.last_cmd == 0x120F
        assert rows[0]["name"] == "测试板块"
        assert rows[0]["block_id"] == 1

    def test_mac_block_members_wiring(self):
        from tstdx.client import MacClient

        pool = _FakePool(_payload_for)
        client = MacClient(pool=pool)
        rows = client.block_members(block_id=1)
        assert pool.last_cmd == 0x1210
        assert rows == [{"code": "600519"}]

    def test_ex_instrument_count_list_wiring(self):
        from tstdx.client import ExMarketClient

        pool = _FakePool(_payload_for)
        client = ExMarketClient(pool=pool)
        rows = client.ex_instrument_count(market=0)
        assert pool.last_cmd == 0x0102
        assert rows == [{"count": 1}]

        rows = client.ex_instrument_list(market=0, start=0)
        assert pool.last_cmd == 0x0103
        assert rows[0]["code"] == "00700"

    def test_ex_market_count_list_wiring(self):
        from tstdx.client import ExMarketClient

        pool = _FakePool(_payload_for)
        client = ExMarketClient(pool=pool)
        rows = client.ex_market_count()
        assert pool.last_cmd == 0x0100
        assert rows == [{"count": 1}]

        rows = client.ex_market_list()
        assert pool.last_cmd == 0x0101
        assert rows[0]["market_id"] == 1

    def test_goods_count_list_wiring(self):
        from tstdx.client import GoodsClient

        pool = _FakePool(_payload_for)
        client = GoodsClient(pool=pool)
        rows = client.goods_count(market=0)
        assert pool.last_cmd == 0x0200
        assert rows == [{"count": 1}]

        rows = client.goods_list(market=0, start=0)
        assert pool.last_cmd == 0x0201
        assert rows[0]["name"] == "螺纹钢"

    def test_async_mirrors_exist_and_dispatch(self):
        from tstdx.client import AsyncExMarketClient, AsyncGoodsClient, AsyncMacClient

        async def run() -> None:
            pool = _AsyncFakePool(_payload_for)
            ex = AsyncExMarketClient(pool=pool)
            rows = await ex.ex_instrument_list(market=0)
            assert pool.last_cmd == 0x0103
            assert rows[0]["code"] == "00700"

            pool2 = _AsyncFakePool(_payload_for)
            goods = AsyncGoodsClient(pool=pool2)
            rows = await goods.goods_list(market=0)
            assert pool2.last_cmd == 0x0201
            assert rows[0]["name"] == "螺纹钢"

            pool3 = _AsyncFakePool(_payload_for)
            mac = AsyncMacClient(pool=pool3)
            rows = await mac.block_list(block_type=0)
            assert pool3.last_cmd == 0x120F
            assert rows[0]["name"] == "测试板块"

            rows = await mac.block_members(block_id=1)
            assert pool3.last_cmd == 0x1210
            assert rows == [{"code": "600519"}]

        asyncio.run(run())

    def test_empty_ex_list_no_body_error(self):
        """ex_instrument_list 空/缺失参数不抛 struct 异常（对齐 block 语义）。"""
        from tstdx.client import ExMarketClient

        pool = _FakePool(_payload_for)
        client = ExMarketClient(pool=pool)
        # 正常路径不校验参数空值——这里仅验证默认参数可用
        rows = client.ex_instrument_list()
        assert pool.last_cmd == 0x0103
        assert len(rows) == 1
