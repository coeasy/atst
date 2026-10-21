"""F2 批次协议正确性回归：P1d doctest 裁决 / P1a 指数尾消费 / P1e 三值矛盾收口 /
prober 三修 / registry 杂项 / parsers __all__ 完整性。
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path

import pytest

from atst.codec.framing import ResponseFrame
from atst.codec.primitive import encode_leb128
from atst.errors import TdxError
from atst.protocol import parsers as parsers_pkg
from atst.protocol.parsers.std7709 import (
    build_realtime_quote_body,
    infer_market,
    quote_request_market,
)
from atst.protocol.parsers.std7709_extra import TradeTodayAltParser, TradeTodayParser
from atst.protocol.prober import Prober
from atst.protocol.registry import PARSERS, BaseParser, dispatch, register_parser

MAGIC = 0x0074CBB1
GOLDEN_ROOT = Path(__file__).resolve().parents[1] / "golden"


def _frame(method: int, payload: bytes, family: str = "quotation") -> ResponseFrame:
    return ResponseFrame(
        magic=MAGIC,
        zip_flag=0,
        seq=0,
        method=method,
        zip_size=len(payload),
        unzip_size=len(payload),
        payload=payload,
    )


def _golden_request_hex(tag: str) -> str:
    cand = sorted(GOLDEN_ROOT.glob(f"quotation/0x0530_realtime_quote_{tag}/20*/meta.json"))
    assert cand, f"缺少 golden 样本: {tag}"
    return json.loads(cand[-1].read_text(encoding="utf-8"))["request"]["body_hex"]


@pytest.mark.unit
class TestP1dRealtimeQuoteBody:
    """P1d 裁决：0x0530 market 反转语义成立，600000 doctest 条目为笔误已修正。

    证据：golden 实测请求体——600000（沪）body_hex=0100…（请求字节 0），
    000651（深）body_hex=0101…（请求字节 1），与 quote_request_market
    的反转规则完全一致；原 doctest 里 600000 写成 0101… 与自家反转函数矛盾。
    """

    def test_quote_request_market_reversal(self):
        assert quote_request_market(1) == 0  # 沪 → 请求字节 0
        assert quote_request_market(0) == 1  # 深 → 请求字节 1
        assert infer_market("600000") == 1
        assert infer_market("000651") == 0

    def test_body_bytes_match_reversal_rule(self):
        assert build_realtime_quote_body("600000").hex() == "0100363030303030"
        assert build_realtime_quote_body("000651").hex() == "0101303030363531"

    def test_body_bytes_match_golden_request(self):
        """实测字节回声：构造的请求体必须与 golden 自采集样本逐字节一致。"""
        for tag in ("600000", "000651", "600519", "601398"):
            assert build_realtime_quote_body(tag).hex() == _golden_request_hex(tag), tag


@pytest.mark.unit
class TestP1aIndexTailConsumption:
    """P1a 解析器侧核实：ctx index=True 时逐 bar 精确消费 4 字节涨跌家数尾。"""

    @staticmethod
    def _bar(dt: int) -> bytes:
        # 4(dt uint32) + 4×1B(leb128 差分) + 8(2×tdx_float) = 16B 最小记录
        return struct.pack("<I", dt) + bytes([0x00, 0x01, 0x02, 0x03]) + b"\x00" * 8

    def test_index_mode_consumes_tail_exactly(self):
        tail = struct.pack("<HH", 900, 100)
        payload = struct.pack("<H", 2) + self._bar(20240102) + tail + self._bar(20240103) + tail
        result = dispatch(_frame(0x052D, payload), category=4, index=True)
        assert result.tier == "L1"
        assert len(result.rows) == 2
        assert result.rows[0]["up_count"] == 900
        assert result.rows[0]["down_count"] == 100
        assert result.meta["reader_meta"] == len(payload), "指数布局未按字节耗尽"

    def test_default_ctx_keeps_stock_behavior(self):
        """ctx 缺省（index=False）保持现行为：不消费尾、无涨跌家数字段。"""
        tail = struct.pack("<HH", 900, 100)
        payload = struct.pack("<H", 2) + self._bar(20240102) + tail + self._bar(20240103) + tail
        result = dispatch(_frame(0x052D, payload), category=4)
        assert result.tier == "L1"
        assert all("up_count" not in row for row in result.rows)
        assert result.meta["reader_meta"] < len(payload)


@pytest.mark.unit
class TestP1eTradeTodayAlignment:
    """P1e：0x0FC5/0x0FC6 布局收口——从 15B 定长猜测改成 leb128 变长（2026-10-21）。

    历史：本类曾把记录钉成 15B 定长（``<H 分钟><f4 价格><f4 量><f4 额><B 方向>``），
    而 golden 样本（count=10 / body 74-76B）根本不能被 15 整除——那版"收口"把
    docstring / RECORD_SIZE / 逐字段消费 三方对齐到了一个**错的**形状上。真机实测
    后记录是变长的：``<H time_minutes>`` + 5 个 leb128（价格差分逐笔累加），
    ``RECORD_SIZE`` 退化为**最短记录**（2 + 5×1 = 7B）守卫门槛，不再等于消费步长。
    """

    @staticmethod
    def _record(
        minute: int = 535,
        price_delta: int = 50,
        vol: int = 20,
        num: int = 3,
        buyorsell: int = 1,
        reserved: int = 0,
    ) -> bytes:
        # <H 分钟> + 5 个 leb128（价格/量/单数/方向/保留）
        return (
            struct.pack("<H", minute)
            + encode_leb128(price_delta)
            + encode_leb128(vol)
            + encode_leb128(num)
            + encode_leb128(buyorsell)
            + encode_leb128(reserved)
        )

    def test_record_size_is_the_minimum_guard_threshold(self):
        # 最短记录 = 2(时间) + 5 个 1 字节 leb128；真实记录可以更长（续行）
        assert TradeTodayParser.RECORD_SIZE == 7
        assert TradeTodayAltParser.RECORD_SIZE == 7
        assert len(self._record()) == 7
        # 带续行的更长记录同样合法，且消耗字节数 > RECORD_SIZE
        long_rec = self._record(price_delta=100000, vol=999999)
        assert len(long_rec) > TradeTodayParser.RECORD_SIZE

    def test_0fc5_guard_threshold_is_consumption_step(self):
        rec = self._record()
        payload = struct.pack("<H", 2) + rec + rec
        result = dispatch(_frame(0x0FC5, payload))
        # 0x0FC5 现在注册 tier="L1"（2026-10-21 双市场 golden 锁定）
        assert result.tier == "L1"
        assert result.meta.get("parser") == "TradeTodayParser"
        assert not any("L1 解析失败" in w for w in result.warnings), result.warnings
        assert len(result.rows) == 2
        # 价格逐笔累加：50 + 50 = 100 → 1.00 元（不是「基准+增量」的 0.50）
        assert result.rows[0]["price"] == pytest.approx(0.50, abs=1e-4)
        assert result.rows[1]["price"] == pytest.approx(1.00, abs=1e-4)
        assert result.rows[0]["volume"] == 20 * 100
        assert result.rows[1]["direction"] == 1
        # 变长记录同样按字节精确耗尽
        assert result.meta["reader_meta"] == len(payload)

    def test_0fc5_price_is_cumulative_not_base_delta(self):
        """与 0x0537 的「基准 + 增量」相反：逐笔是累加。"""
        payload = struct.pack("<H", 3) + b"".join(
            self._record(minute=571 + i, price_delta=d) for i, d in enumerate((100, 5, -3))
        )
        result = dispatch(_frame(0x0FC5, payload))
        assert [round(r["price"], 4) for r in result.rows] == [1.0, 1.05, 1.02]

    def test_0fc6_same_layout(self):
        rec = self._record()
        payload = struct.pack("<H", 1) + rec
        result = dispatch(_frame(0x0FC6, payload))
        assert result.meta.get("parser") == "TradeTodayAltParser"
        assert not any("L1 解析失败" in w for w in result.warnings), result.warnings
        assert len(result.rows) == 1
        assert result.meta["reader_meta"] == len(payload)


@pytest.mark.unit
class TestCapitalChangesShortTail:
    """0x000F 尾记录短于声明步长时停止解析（消除裸 struct.error）。"""

    def test_trailing_partial_record_breaks(self):
        """块内声明条数超过实际数据时停止解析（消除裸 struct.error）。

        载荷按 2026-10-21 实测锁定的两级结构搭建：``<H blocks><块头 9B><num 条记录>``。
        收口前：rec 短于 size 时 rec[7]/struct.unpack_from 产生裸 IndexError/
        struct.error 且整批解析崩溃；收口后：钳制到 1 条完整记录 + 告警。
        """
        rec = b"\x01" + b"600000" + b"\x01" + b"\x00" * 21  # 29B 完整记录
        # 1 个证券块声明 3 条，实际只有 1 条完整 + 2B 残尾
        payload = (
            struct.pack("<H", 1) + b"\x01" + b"600000" + struct.pack("<H", 3) + rec + b"\x02\xab"
        )
        result = dispatch(_frame(0x000F, payload), record_size=29)
        assert len(result.rows) == 1
        assert result.rows[0]["code"] == "600000"
        assert any("钳制" in w for w in result.warnings), result.warnings
        assert not any("L1 解析失败" in w for w in result.warnings), result.warnings

    def test_adaptive_size_still_parses_all(self):
        # 无 ctx record_size 时按 RECORD_SIZE（29B）解析，两条完整记录全部解出
        rec = b"\x01" + b"600000" + b"\x01" + b"\x00" * 21
        payload = struct.pack("<H", 1) + b"\x01" + b"600000" + struct.pack("<H", 2) + rec + rec
        result = dispatch(_frame(0x000F, payload))
        assert len(result.rows) == 2
        assert result.rows[1]["code"] == "600000"
        # raw 必须完整保留（dispatch 侧原文透传，与解析器消费方式无关）
        assert result.raw == payload


@pytest.mark.unit
class TestProberContracts:
    """prober 三修：rate_limit 校验 / probe_range 区间钳制 / 盘中守卫与 docstring 对齐。"""

    def test_rate_limit_must_be_positive(self):
        with pytest.raises(ValueError, match="rate_limit"):
            Prober(rate_limit=0)
        with pytest.raises(ValueError, match="rate_limit"):
            Prober(rate_limit=-1.5)

    def test_probe_range_max_count_clamps_interval(self):
        prober = Prober(rate_limit=1e6, block_offline_only=False)  # client=None → dry-run
        results = prober.probe_range(0x1000, 0x2000, max_count=5)
        # 命令号被钳到 5 个，每个 × 2 市场 = 10 次探测（而非 4097×2 次）
        assert len(results) == 10
        assert sorted({r.cmd_id for r in results}) == [0x1000, 0x1001, 0x1002, 0x1003, 0x1004]

    def test_probe_range_max_count_must_be_positive(self):
        prober = Prober(rate_limit=1e6, block_offline_only=False)
        with pytest.raises(ValueError, match="max_count"):
            prober.probe_range(0x1000, 0x1002, max_count=0)

    def test_trading_hours_returns_failure_result_by_default(self):
        prober = Prober(rate_limit=1e6)
        prober.only_offline_hours = lambda **kwargs: False  # 强制盘中
        result = prober.probe_command(0x1234, market=1, code="600519")
        assert result.ok is False
        assert result.raw_response == b""
        assert any("trading hours" in n for n in result.notes), result.notes
        assert any("Probing hits real TDX hosts" in n for n in result.notes)
        assert prober.total_probes == 0  # 未发出任何请求

    def test_strict_mode_raises_during_trading_hours(self):
        prober = Prober(rate_limit=1e6, strict=True)
        prober.only_offline_hours = lambda **kwargs: False
        with pytest.raises(TdxError, match="blocked during trading hours"):
            prober.probe_command(0x1234)


@pytest.mark.unit
class TestRegistryMisc:
    """registry 杂项：降级告警常量 / 幂等顶替 debug log / 解析器告警缓冲。"""

    def test_degrade_notice_constant(self):
        from atst.protocol.registry import DEGRADE_NOTICE

        assert DEGRADE_NOTICE == "精确解析失败已降级启发式，字段映射可能不完整"

    def test_parser_warn_ctx_via_parse_result(self):
        """warn_ctx 写入的告警必须出现在 ParseResult.warnings。"""
        cmd = 0x7F10
        try:

            @register_parser(cmd, family="quotation", name="WARN_CTX_TEMP")
            class WarnCtxTempParser(BaseParser):
                def parse_payload(self, reader, **ctx):
                    BaseParser.warn_ctx(ctx, "合成告警：测试钳制/截断留痕通道")
                    return [{"n": reader.uint16()}]

            result = dispatch(_frame(cmd, b"\x01\x00"))
            assert result.rows == [{"n": 1}]
            assert "合成告警：测试钳制/截断留痕通道" in result.warnings
        finally:
            PARSERS.pop(("quotation", cmd), None)

    def test_cross_module_same_name_registration_logs_debug(self, caplog):
        """跨模块同名解析器幂等顶替失败路径必须留 debug log。"""
        key = ("quotation", 0x7F11)
        try:

            @register_parser(0x7F11, family="quotation", name="SAME_NAME_TEMP")
            class SameNameTempParser(BaseParser):
                def parse_payload(self, reader, **ctx):
                    return []

            first = PARSERS[key]

            duplicate = type("SameNameTempParser", (BaseParser,), {})
            duplicate.__module__ = "atst.protocol.parsers.from_another_module"
            decorated = register_parser(0x7F11, family="quotation", name="SAME_NAME_TEMP")(
                duplicate
            )
            assert decorated is duplicate
            assert PARSERS[key] is first  # 保留先注册者

            with caplog.at_level(logging.DEBUG, logger="atst.protocol.registry"):
                register_parser(0x7F11, family="quotation", name="SAME_NAME_TEMP")(duplicate)

            assert "幂等顶替" in caplog.text, caplog.text
        finally:
            PARSERS.pop(key, None)


@pytest.mark.unit
class TestP1cDiffBase:
    """P1c：0x1300 / 0x0104 差分基准行为锁——**锁现状**（per-record absolute）。

    0x052D/0x0202 用跨记录 running base（open_abs = open_diff + 上一条
    close_abs）；0x1300/0x0104 当前实现每条独立（open 直接取 leb128 绝对值），
    与 docstring 的「同构」声明存在已知偏差。两命令从未真机确认，布局待样本
    锁定：本测试锁住当前行为，防止实现被"顺手对齐 0x052D"而静默翻转；
    将来真机样本判定为跨记录基准时，与实现一起翻转本测试。
    """

    A = 1 << 20  # 21 bit → leb128 编码 4 字节，保证单条记录达到 28B 守卫门槛
    D = 1 << 20

    def _record(self, open_v: int, oi: bool = False) -> bytes:
        # <I dt> + leb128(open, close_diff, high_diff, low_diff) + 2×tdx_float [ + <I oi> ]
        rec = (
            struct.pack("<I", 20240102)
            + encode_leb128(open_v)
            + encode_leb128(self.D) * 3
            + struct.pack("<II", 0, 0)
        )
        if oi:
            rec += struct.pack("<I", 0)
        return rec

    def test_mac_0x1300_record2_not_accumulated(self):
        rec1 = self._record(self.A)
        rec2 = self._record(3 * self.A)
        payload = struct.pack("<H", 2) + rec1 + rec2
        result = dispatch(_frame(0x1300, payload), family="mac_quotation")
        assert result.meta.get("parser") == "MacUnifiedBarsParser"
        assert len(result.rows) == 2
        # 记录 1：open=A，close=A+D（本条内相对 open）
        assert result.rows[0]["open"] == round(self.A / 1000, 4)
        assert result.rows[0]["close"] == round((self.A + self.D) / 1000, 4)
        # 记录 2 的 open 直接是 3A：**不**累加记录 1 的 close（跨记录 base 会给出 5A）
        assert result.rows[1]["open"] == round((3 * self.A) / 1000, 4)
        assert result.rows[1]["close"] == round((3 * self.A + self.D) / 1000, 4)
        # 完整解析不产生任何告警（含 §2-17 截断告警）
        assert result.warnings == []

    def test_ex_0x0104_record2_not_accumulated(self):
        rec1 = self._record(self.A, oi=True)
        rec2 = self._record(3 * self.A, oi=True)
        payload = struct.pack("<H", 2) + rec1 + rec2
        result = dispatch(_frame(0x0104, payload), family="ex_quotation")
        assert result.meta.get("parser") == "ExInstrumentBarsParser"
        assert len(result.rows) == 2
        assert result.rows[0]["open"] == round(self.A / 1000, 4)
        assert result.rows[1]["open"] == round((3 * self.A) / 1000, 4)  # 不累加（同上）
        assert result.rows[1]["open_interest"] == 0
        assert result.warnings == []


@pytest.mark.unit
class TestTruncationObservability:
    """§2-17 补漏：count 循环因 remaining 耗尽中途 break 时不得静默丢行。

    机制：guarded_count 把声明数登记到解析状态盒，BaseParser.parse 在
    rows < 声明且未发生钳制时记「记录截断：声明 N 实收 M」——
    SecurityListParser 自洽校验（rows != count 即 raise）的温和版，
    覆盖 std7709/std7709_extra/std7727/mac/goods/f10 全部 count 驱动循环。
    """

    @staticmethod
    def _bar20() -> bytes:
        # 20B K 线：4(dt) + 4×leb128(100)=8 + 8(tdx_float×2) —— 大于 16B 最小门槛
        return struct.pack("<I", 20240102) + encode_leb128(100) * 4 + struct.pack("<II", 0, 0)

    def test_security_bars_truncated_rows_warned(self):
        # 声明 10 条，只给 9 条完整（180B）+ 10B 残尾：循环 break，rows=9
        payload = struct.pack("<H", 10) + self._bar20() * 9 + b"\xaa" * 10
        result = dispatch(_frame(0x052D, payload), category=4)
        assert result.meta.get("parser") == "SecurityBarsParser"
        assert len(result.rows) == 9
        trunc = [w for w in result.warnings if "记录截断" in w]
        assert trunc, result.warnings
        assert "声明 10 条" in trunc[0] and "实收 9 条" in trunc[0]
        assert not any("钳制" in w for w in result.warnings)  # 未钳制 → 不重复告警

    def test_clamped_case_not_double_warned(self):
        # 钳制场景由「count 失真已钳制」告警覆盖（消息已含声明数与上限），
        # 不再叠加「记录截断」
        payload = struct.pack("<H", 65535) + b"\xff" * 45
        result = dispatch(_frame(0x120F, payload), family="mac_quotation")
        assert len(result.rows) == 4
        assert any("钳制" in w for w in result.warnings)
        assert not any("记录截断" in w for w in result.warnings)

    def test_complete_parse_has_no_truncation_warning(self):
        payload = struct.pack("<H", 3) + self._bar20() * 3
        result = dispatch(_frame(0x052D, payload), category=4)
        assert len(result.rows) == 3
        assert not any("记录截断" in w for w in result.warnings)


@pytest.mark.unit
class TestP2DeadCodeCleanup:
    """P2 微增补：死代码三小项（get_datetime_from_lc 单参化 / _count_records 删除 /
    MacHeartbeat 恒真定性）。"""

    def test_get_datetime_from_lc_single_param_contract(self):
        from atst.codec.primitive import get_datetime_from_lc

        # 公式原样：num=0 → (2004, 0, 0)（不做月日归一，语义与消费点一致）
        assert get_datetime_from_lc(0) == (2004, 0, 0)
        assert get_datetime_from_lc(2048 * 22 + 517) == (2026, 5, 17)
        with pytest.raises(TypeError):
            get_datetime_from_lc(1, 0)  # 第二形参已删除（契约变更，见 CHANGELOG）

    def test_mac_heartbeat_alive_is_design(self):
        # 空 payload 也判活 = 设计：dispatch 抵达本解析器即证明帧已收到且自洽，
        # 真实主站心跳响应常态就是空载荷/单字节
        result = dispatch(_frame(0x2562, b""), family="mac_quotation")
        assert result.meta.get("parser") == "MacHeartbeatParser"
        assert result.rows == [{"alive": True}]

    def test_mac_count_records_helper_removed(self):
        import atst.protocol.parsers.mac as mac_mod

        # 与各解析器循环体重复实现且全库零引用的死代码，F2 已收敛删除
        assert not hasattr(mac_mod, "_count_records")


@pytest.mark.unit
class TestParsersPackageExports:
    """parsers/__init__.py __all__ 对照实际导出补全（P2 #2 顺手项）。"""

    def test_dunder_all_matches_public_names(self):
        import types

        public = {
            name
            for name in dir(parsers_pkg)
            if not name.startswith("_")
            and not isinstance(getattr(parsers_pkg, name), types.ModuleType)
        }
        declared = set(parsers_pkg.__all__)
        assert declared == public, (
            f"__all__ 缺失: {sorted(public - declared)}; 多余: {sorted(declared - public)}"
        )
        assert len(declared) >= 55

    def test_all_names_resolvable(self):
        for name in parsers_pkg.__all__:
            assert getattr(parsers_pkg, name, None) is not None, name
