# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""原生加速内核加载层测试（C4）。

本环境通常没有编译好的 ``tstdx_native`` 扩展，因此这些测试聚焦：
1. 加载层可导入、结构完整；
2. 无扩展时**透明回退**纯 Python 实现，且输出与参考实现逐字段一致；
3. ``selftest()`` 诊断结构稳定，供 CI 断言。

（Rust 扩展构建与对拍在 .github/workflows/native.yml 中验证。）
"""

from __future__ import annotations

import struct

import pytest

from tstdx.native import (
    NATIVE_AVAILABLE,
    decode_tdx_float,
    disable_reason,
    native,
    parse_kline_payload,
    read_day_file,
    selftest,
)

TDX_FLOAT_VECTORS = (
    0x00000000,
    0x00000001,
    0x0000007F,
    0x00000080,
    0x0000FFFF,
    0x00FFFFFF,
    0x01000000,
    0x7F000001,
    0x80000000,
    0xFFFFFFFF,
)


def _make_day_bytes(n: int = 3) -> bytes:
    # 自洽数据：8,000,000 元 / 800,000 股 = 10.0 ≈ (11.0+9.0)/2（保证 scale 探测正确）
    rec = struct.pack("<IIIIIfII", 20260831, 1000, 1100, 900, 1050, 8_000_000.0, 800_000, 1000)
    return rec * n


def test_module_exports_are_present() -> None:
    """加载层公开符号完整。"""
    if NATIVE_AVAILABLE:
        assert native is not None
        assert disable_reason is None
    else:
        assert native is None
        assert isinstance(disable_reason, str) and disable_reason
    assert isinstance(NATIVE_AVAILABLE, bool)


def test_fallback_active_without_compiled_extension() -> None:
    """未安装编译扩展时禁用原生层并给出原因；已安装且自检通过时跳过。"""
    if NATIVE_AVAILABLE:
        pytest.skip("原生扩展已安装且自检通过，回退路径不适用")
    assert NATIVE_AVAILABLE is False
    assert native is None
    assert disable_reason  # 有明确原因


def test_decode_tdx_float_matches_primitive_reference() -> None:
    """回退实现与 codec.primitive 参考逐值一致。"""
    from tstdx.codec.primitive import decode_tdx_float as ref

    for raw in TDX_FLOAT_VECTORS:
        assert decode_tdx_float(raw) == ref(raw)
        assert abs(decode_tdx_float(raw) - ref(raw)) < 1e-9


def test_read_day_file_matches_formats_reference(tmp_path) -> None:
    """回退实现与 reader.formats 参考逐字段一致。"""
    from tstdx.reader.formats import read_day_file as ref

    p = tmp_path / "sh600000.day"
    p.write_bytes(_make_day_bytes())

    got = read_day_file(p)
    want = ref(p)
    assert got == want
    assert isinstance(got, list) and len(got) == 3


def _make_kline_payload(n: int = 2, *, category: int = 4, index: bool = False) -> bytes:
    """与 native.py `_make_kline_payload` 同构的 0x052D 合成载荷。"""
    from tstdx.codec.primitive import encode_leb128
    from tstdx.protocol.parsers.std7709 import DAYLIKE_CATEGORIES

    daylike = category in DAYLIKE_CATEGORIES
    out = bytearray(struct.pack("<H", n))
    for i in range(n):
        if daylike:
            out += struct.pack("<I", 20_260_102 + i)
        else:
            lc16 = (2026 - 2004) * 2048 + 1 * 100 + 2  # 2026-01-02
            out += struct.pack("<HH", lc16, 9 * 60 + 30)  # 09:30
        out += encode_leb128(10_000)  # open diff
        out += encode_leb128(500)  # close diff
        out += encode_leb128(1_400)  # high diff
        out += encode_leb128(-1_000)  # low diff
        out += struct.pack("<I", 0x4270_0000)  # tdx_float → 60.0
        out += struct.pack("<I", 0x4000_0000)  # tdx_float → 2.0
    if index:
        out += struct.pack("<HH", 1234, 56)
    return bytes(out)


def test_parse_kline_payload_matches_reference() -> None:
    """回退实现与 SecurityBarsParser 参考逐字段一致（日线/周线/分钟/指数）。"""
    from tstdx.codec.primitive import BinaryReader
    from tstdx.protocol.parsers.std7709 import SecurityBarsParser

    scenarios = (
        {"category": 4, "index": False},  # 日线
        {"category": 5, "index": False},  # 周线（lot ×100）
        {"category": 0, "index": False},  # 分钟线
        {"category": 4, "index": True},  # 指数
    )
    for sc in scenarios:
        payload = _make_kline_payload(category=sc["category"], index=sc["index"])
        got = parse_kline_payload(
            payload, category=sc["category"], price_scale=1000.0, index_mode=sc["index"]
        )
        want = SecurityBarsParser().parse_payload(
            BinaryReader(payload),
            category=sc["category"],
            price_scale=1000,
            index=sc["index"],
        )
        assert len(got) == len(want), f"category={sc['category']} index={sc['index']}"
        for g, w in zip(got, want, strict=False):
            assert g["datetime"] == w["datetime"]
            assert g["date"] == w["date"]
            assert g["time"] == w["time"]
            for k in ("open", "close", "high", "low", "amount"):
                assert abs(float(g[k]) - float(w[k])) < 1e-6, k
            assert int(g["volume"]) == int(w["volume"])
            for k in ("up_count", "down_count"):
                assert g.get(k) == w.get(k), k


def test_selftest_structure() -> None:
    """selftest() 返回稳定诊断结构，供 CI 断言。"""
    result = selftest()
    assert set(result) >= {
        "importable",
        "native_available",
        "disable_reason",
        "checks",
    }
    if result["native_available"]:
        # 扩展已安装且自检通过：必须无禁用原因，且逐项检查全部 ok
        assert result["disable_reason"] is None
        assert all(v == "ok" for v in result["checks"].values())
    else:
        # 扩展不可用：必须给出明确禁用原因（未安装 / 自检失败 / 非编译扩展）
        assert result["disable_reason"]
