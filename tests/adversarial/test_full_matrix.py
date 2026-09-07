# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""对抗 payload 全矩阵门禁（工业审计 F5 固化）。

复刻 2026-09 审计的一次性探针为**永久回归**：对命令账本的每个 (family, cmd)，
用 9 类畸形响应字节构造 :class:`ResponseFrame` 驱动 ``registry.dispatch``，
断言三条工业不变量：

1. **异常面契约**：只允许 ``TdxError`` 系逃逸（ParseError/GenericParse 等），
   任何内建异常（TypeError/ValueError/IndexError/struct.error/...）逃逸
   即违反「L1→L2→L3 永不丢包」承诺——在流式路径会杀死轮询线程。
2. **可驻留性**：单条 dispatch < 1.0s（审计实测最慢 3.4s 的畸形输入
   慢解析必须被 count 钳制压下来）。
3. **确定性**：固定随机种子，崩溃点可复现。

运行：pytest tests/adversarial -q（全矩阵约 765 例，秒级）。
"""

from __future__ import annotations

import random
import struct
import time
from typing import Any

from tstdx.codec.framing import ResponseFrame
from tstdx.errors import TdxError
from tstdx.protocol.commands import COMMANDS
from tstdx.protocol.registry import dispatch

_MAGIC = 0x12566312  # 与 codec/framing 默认 magic 一致（仅用于让 ok 判定不干扰解析）


def test_gate_has_teeth_canary() -> None:
    """自我验证门禁检测 machinery（F2 边界收口后的新契约版）。

    P1b 收口落地后，dispatch 必须把解析器逃逸的原生异常**包装为
    ParseError 并保留 cause**（registry.py 边界收口）。canary 注入一个
    必抛 struct.error 的临时解析器，断言：① 逃逸面只剩 ParseError（TdxError
    系）；② context 记录原始异常类型；③ __cause__ 保留原生异常——若有人
    移除包装层，原生 struct.error 将直接穿透，本测试立即失败。检测
    「收口被破坏」而非「原生异常穿透」，语义等价地保留门禁的牙齿。
    """
    import struct

    import pytest

    from tstdx.errors import ParseError
    from tstdx.protocol.registry import PARSERS, BaseParser, register_parser

    @register_parser(0x7FE0, family="quotation")
    class _Boom(BaseParser):
        def parse(self, frame: Any, **ctx: Any) -> Any:  # noqa: ANN401
            raise struct.error("canary escape")

    key = ("quotation", 0x7FE0)
    assert key in PARSERS, "register_parser API 变更导致 canary 失效——修此测试"
    try:
        with pytest.raises(ParseError) as ei:
            dispatch(
                _make_frame(0x7FE0, b"\x00\x00"),
                family="quotation",
                allow_generic=False,
                market=1,
                code="600000",
                category=9,
                price_scale=100,
            )
        exc = ei.value
        assert isinstance(exc.__cause__, struct.error), (
            "ParseError 未保留原生 cause——边界收口包装被弱化（门禁失效）"
        )
        ctx = exc.context or {}
        assert "error" in str(ctx.get("exception_type", "")), (
            f"context.exception_type 缺失/漂移: {ctx!r}"
        )
        assert ctx.get("command") == hex(0x7FE0), f"context.command 未记录: {ctx!r}"
    finally:
        PARSERS.pop(key, None)


def _make_frame(cmd: int, body: bytes) -> ResponseFrame:
    return ResponseFrame(
        magic=_MAGIC,
        zip_flag=0x0B,
        seq=0,
        method=cmd,
        zip_size=len(body),
        unzip_size=len(body),
        body=body,
        payload=body,
    )


def _payloads() -> dict[str, bytes]:
    rnd = random.Random(20260902)
    return {
        "huge_count16": b"\xff\xfe" * 64,
        "zero": b"\x00" * 128,
        "all_ff": b"\xff" * 128,
        "all_00_pad": b"\x00" * 512,
        "neg_leb": b"\x80\x80\x80\x80\x78" * 16,
        "float_max": struct.pack("<f", 3.4028235e38) * 32,
        "random": bytes(rnd.randrange(256) for _ in range(256)),
        "short": b"\x01",
        "empty": b"",
    }


def _iter_ledger() -> list[tuple[str, int]]:
    return sorted({(fam, cmd) for (fam, cmd) in COMMANDS})


def _run_matrix(*, allow_generic: bool) -> tuple[list[str], list[str], int]:
    """allow_generic=False 强制走 L1 精确解析器——原生异常逃逸才暴露。

    降级到 L2/L3 会吞掉解析器崩溃（掩盖契约违反），故主门禁必须 strict-L1。
    """
    payloads = _payloads()
    escaped: list[str] = []
    slow: list[str] = []
    n = 0
    for fam, cmd in _iter_ledger():
        for pname, body in payloads.items():
            n += 1
            t0 = time.perf_counter()
            try:
                dispatch(
                    _make_frame(cmd, body),
                    family=fam,
                    allow_generic=allow_generic,
                    market=1,
                    code="600000",
                    category=9,
                    price_scale=100,
                )
            except TdxError:
                pass  # 契约内：解析失败必须归入 TdxError 系
            except BaseException as exc:  # noqa: BLE001 - 门禁正是抓这个
                escaped.append(
                    f"cmd=0x{cmd:04x} fam={fam} payload={pname}: "
                    f"{type(exc).__name__}: {str(exc)[:80]}"
                )
            dt = time.perf_counter() - t0
            if dt > 1.0:
                slow.append(f"cmd=0x{cmd:04x} fam={fam} payload={pname}: {dt:.2f}s")
    return escaped, slow, n


def test_adversarial_matrix_strict_l1_no_escape() -> None:
    """主门禁（strict-L1）：精确解析器 0 原生异常逃逸 + 0 慢解析。"""
    escaped, slow, n = _run_matrix(allow_generic=False)
    assert n > 500, f"矩阵覆盖异常偏小（{n}），账本遍历逻辑可能失效"
    assert not escaped, "L1 解析器原生异常逃逸（违反永不丢包契约）:\n" + "\n".join(escaped[:40])
    assert not slow, "畸形输入慢解析（需 count 钳制）:\n" + "\n".join(slow[:40])


def test_adversarial_matrix_degraded_path_no_escape() -> None:
    """降级门禁：L2/L3 兜底路径同样不得逃逸原生异常（永不丢包承诺）。"""
    escaped, _slow, n = _run_matrix(allow_generic=True)
    assert n > 500
    assert not escaped, "降级路径原生异常逃逸:\n" + "\n".join(escaped[:40])


def test_adversarial_known_crash_sites_are_tdxerror() -> None:
    """审计实测崩溃锚点的精确回归：这些 payload 曾逃逸原生异常。"""
    import pytest

    cases = [
        ("goods", 0x020A, b"\xff" * 128, "goods TypeError(_tdx_gbk)"),
        ("goods", 0x0206, b"\xff" * 128, "goods TypeError"),
        ("mac_quotation", 0x1300, b"\x80" * 128, "mac ValueError(负count)"),
        ("mac_quotation", 0x1300, b"\xff" * 128, "mac ValueError(all_ff)"),
        ("quotation", 0x052D, b"\xff" * 256, "std7709 float-too-large"),
        ("quotation", 0x052D, b"\x80" * 256, "std7709 无法解码字节"),
    ]
    for fam, cmd, body, note in cases:
        try:
            dispatch(
                _make_frame(cmd, body),
                family=fam,
                market=1,
                code="600000",
                category=9,
                price_scale=100,
            )
        except TdxError:
            pass
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{note}: 逃逸 {type(exc).__name__}，应为 TdxError 系")
