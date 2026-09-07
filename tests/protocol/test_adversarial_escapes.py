"""P1b/T2 对抗逃逸矩阵测试（审计附录 A「9 畸形字节 × 命令」矩阵的 CI 缩样版）。

背景（INDUSTRIAL_OPTIMIZATION_PLAN.md P1b/T2/T3）：
历史实测中 ``goods``（×7 cmd TypeError）、``mac``（负 count ValueError）、
``std7709``（float 过大 / bytes range）等解析器存在**原生异常逃逸**——
非 ``TdxError`` 子类，客户端 ``except TdxError`` 接不住，畸形主站响应
会杀死轮询线程。

本模块用同一批畸形 payload 直接复刻审计锚点，断言：

1. **无原生异常逃逸**：dispatch 边界只允许 ``TdxError`` 子类抛出
   （原生异常必须被包装为 :class:`~tstdx.errors.ParseError`，保留 cause 与
   ``cmd/family/异常类型`` 上下文）；
2. **fatal IntegrityViolation 原样上抛**（「致命不降级」契约不被旁路）；
3. L1 失败降级 L2 时 warnings 必须携带降级告警（全 0 Bar 类静默可观测）；
4. count 失真钳制生效并记录 warning（T3 慢解析防御）。
"""

from __future__ import annotations

import random
import struct
import time

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.errors import IntegrityViolation, ParseError, TdxError
from tstdx.protocol import parsers  # noqa: F401  触发全部解析器注册
from tstdx.protocol.commands import Family
from tstdx.protocol.registry import DEGRADE_NOTICE, PARSERS, BaseParser, dispatch, register_parser

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


#: 审计对抗矩阵缩样：9 类畸形字节（含「全 0xFF 声明巨量 count」「随机流」两类慢解析源）
ADVERSARIAL_PAYLOADS = {
    "empty": b"",
    "one_byte_ff": b"\xff",
    "count65535_head": b"\xff\xff",
    "count_mismatch_tiny": b"\x2c\x01\x01",
    "all_ff_64": b"\xff" * 64,
    "all_ff_2048": b"\xff" * 2048,
    "random_512": bytes(random.Random(42).randrange(256) for _ in range(512)),
    "leb128_max_chain": b"\x04\x00" + b"\x7f" * 64,
    "short_header": b"\x01\x02\x03",
}

#: 覆盖审计锚点所在的三个协议族（goods / mac / std7709 标准）
FAMILIES_UNDER_TEST = (Family.GOODS, Family.MAC, Family.STANDARD)


@pytest.mark.unit
class TestAdversarialNoNativeEscape:
    """对抗矩阵：畸形 payload 经 dispatch 只允许 TdxError 或正常 ParseResult。"""

    def test_matrix_no_native_escape(self):
        from tstdx.protocol.registry import registered_ids

        escapes: list[str] = []
        checked = 0
        for family in FAMILIES_UNDER_TEST:
            for _fam, cmd in registered_ids(family):
                for name, payload in ADVERSARIAL_PAYLOADS.items():
                    checked += 1
                    try:
                        dispatch(_frame(cmd, payload), family=family)
                    except TdxError:
                        pass  # ParseError / IntegrityViolation 均为合法边界输出
                    except Exception as exc:  # 原生异常逃逸 = 缺陷
                        escapes.append(f"{family}/0x{cmd:04x}[{name}] {type(exc).__name__}: {exc}")
        assert checked >= 100, f"矩阵覆盖不足: {checked}"
        assert not escapes, f"原生异常逃逸 {len(escapes)} 处:\n" + "\n".join(escapes)

    def test_goods_legacy_type_error_anchors_now_parse_error(self):
        """审计锚点复测：goods 族 7 个历史 TypeError 命令不再逃逸原生异常。"""
        from tstdx.protocol.registry import registered_ids

        goods_cmds = [cmd for _fam, cmd in registered_ids(Family.GOODS)]
        assert len(goods_cmds) >= 7
        for cmd in goods_cmds:
            for payload in (b"\xff" * 96, b"\x00" * 96, b"\x2c\x01" + b"\xff" * 30):
                try:
                    dispatch(_frame(cmd, payload), family=Family.GOODS)
                except TdxError as exc:
                    # 逃逸面收口后：只允许 ParseError 系（含 fatal），绝无 TypeError
                    assert isinstance(exc, ParseError), f"0x{cmd:04x}: {type(exc).__name__}"

    def test_mac_legacy_value_error_anchors_now_parse_error(self):
        """审计锚点复测：mac.py 历史负 count（:61/:92）不再逃逸 ValueError。"""
        for cmd in (0x120F, 0x1300):
            # 全 0xFF：count 读出 0xFFFF（历史版本按有符号读为负 → ValueError）
            try:
                dispatch(_frame(cmd, b"\xff" * 256), family=Family.MAC)
            except TdxError as exc:
                assert isinstance(exc, ParseError), f"0x{cmd:04x}: {type(exc).__name__}"


@pytest.mark.unit
class TestDispatchBoundaryWrap:
    """dispatch 边界收口：原生异常统一包装、fatal 原样、L2 侧同语义。"""

    @staticmethod
    def _register_temp_native(cmd: int, exc: Exception) -> None:
        @register_parser(cmd, family=Family.GOODS, name="ADVERSARIAL_TEMP")
        class AdversarialTempParser(BaseParser):
            def parse(self, frame, **ctx):
                # 模拟「框架级逃逸」：不经 BaseParser.parse 包装直接抛原生异常
                raise exc

            def parse_payload(self, reader, **ctx):  # pragma: no cover
                return []

    def test_native_type_error_wrapped_as_parse_error(self):
        cmd = 0x7F01
        try:
            self._register_temp_native(cmd, TypeError("boom: int has no .decode"))
            with pytest.raises(ParseError) as ei:
                dispatch(_frame(cmd, b"\x00" * 8), family=Family.GOODS)
            ctx = ei.value.context
            assert ctx["command"] == hex(cmd)
            assert ctx["family"] == Family.GOODS
            assert ctx["exception_type"] == "TypeError"
            assert isinstance(ei.value.__cause__, TypeError)
        finally:
            PARSERS.pop((Family.GOODS, cmd), None)

    def test_fatal_integrity_violation_reraised_unchanged(self):
        cmd = 0x7F02
        try:
            self._register_temp_native(cmd, IntegrityViolation("回声校验失败", context={"k": "v"}))
            with pytest.raises(IntegrityViolation) as ei:
                dispatch(_frame(cmd, b"\x00" * 8), family=Family.GOODS)
            assert ei.value.context.get("k") == "v"  # 原样：未被二次包装
        finally:
            PARSERS.pop((Family.GOODS, cmd), None)

    def test_native_escape_from_l2_generic_wrapped(self, monkeypatch):
        def _boom(frame, *, family):
            raise struct.error("unpack requires a buffer of 4 bytes")

        monkeypatch.setattr("tstdx.protocol.generic.parse_generic", _boom)
        # 0x9999 无 L1 解析器 → 走 L2 通用解析
        with pytest.raises(ParseError) as ei:
            dispatch(_frame(0x9999, b"\x00" * 8))
        # struct.error 的 type.__name__ 是 "error"
        assert ei.value.context["exception_type"] == "error"
        assert ei.value.context["family"] == Family.STANDARD
        assert isinstance(ei.value.__cause__, struct.error)

    def test_l1_degrade_notice_observable(self):
        """0x0530 非 fatal 解析失败 → 降级 L2/L3 且 warnings 携带降级告警。"""
        # 7 字节头 + 5 字节非零尾：过得了空载荷检测，但不足以容纳已验证字段
        payload = b"\x01" + b"600000" + b"\x41\x42\x43\x44\x45"
        result = dispatch(_frame(0x0530, payload), code="600000", market=1, price_scale=100)
        assert result.tier in ("L2", "L3")
        assert any(w.startswith("L1 解析失败") for w in result.warnings), result.warnings
        assert DEGRADE_NOTICE in result.warnings


@pytest.mark.unit
class TestCountClampT3:
    """T3：count 失真钳制——声明数收敛到剩余字节容纳上限，并记 1 条 warning。"""

    def test_mac_block_list_clamp(self):
        # 声明 65535 条，实际只剩 45B（每条 10B → 容纳 4 条）
        payload = struct.pack("<H", 65535) + b"\xff" * 45
        result = dispatch(_frame(0x120F, payload), family=Family.MAC)
        assert len(result.rows) == 4
        assert any("钳制" in w for w in result.warnings), result.warnings

    def test_security_bars_clamp(self):
        # 声明 800 条，实际只剩 30B（K 线最小记录 16B → 容纳 1 条）
        payload = struct.pack("<H", 800) + b"\x00" * 30
        result = dispatch(_frame(0x052D, payload), category=4)
        assert len(result.rows) == 1
        assert any("钳制" in w for w in result.warnings), result.warnings

    def test_mac_all_ff_stream_fast(self):
        """全 0xFF 流（慢解析锚点输入）解析耗时必须 < 0.2s。"""
        payload = b"\xff" * (1 << 15)
        t0 = time.perf_counter()
        result = dispatch(_frame(0x1300, payload), family=Family.MAC)
        elapsed = time.perf_counter() - t0
        assert result is not None
        assert elapsed < 0.2, f"mac 全 0xFF 流解析耗时 {elapsed:.3f}s >= 0.2s"

    def test_capital_changes_short_tail_breaks_without_struct_error(self):
        """ctx record_size 大于实际数据时：钳制 + 尾记录停止解析，无裸 struct.error。"""
        rec = b"\x01" + b"600000" + b"\x01" + b"\x00" * 21  # 29B
        payload = struct.pack("<H", 3) + rec + b"\x02\xab\xcd"  # 声明 3 条，只有 1 条完整
        result = dispatch(_frame(0x000F, payload), record_size=29)
        assert len(result.rows) == 1
        assert result.rows[0]["code"] == "600000"
        assert any("钳制" in w for w in result.warnings), result.warnings


@pytest.mark.unit
class TestFatalContractBypass:
    """「致命不降级」契约：批量解析器的内层 except 不得吞掉 IntegrityViolation。"""

    def test_quotes_snapshot_fatal_reraised(self):
        # 单只解析遇「全零载荷」（占位响应检测）→ IntegrityViolation（fatal）必须上抛
        payload = struct.pack("<H", 2) + b"\x01" + b"600000" + b"\x00" * 20
        with pytest.raises(IntegrityViolation):
            dispatch(_frame(0x054C, payload))

    def test_quotes_snapshot_non_fatal_stops_batch_gracefully(self):
        # 单只解析遇「过短段」（非 fatal）→ 回退位置、记 warning、优雅停止
        payload = struct.pack("<H", 2) + b"\x01" + b"600000" + b"\x41\x42"
        result = dispatch(_frame(0x054C, payload))
        assert result.tier in ("L2", "L3")
        assert any("L1 解析失败" in w for w in result.warnings), result.warnings
