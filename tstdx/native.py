# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Retired native-acceleration compatibility layer.

The historical ``tstdx_native`` Rust extension source is not part of this
repository. Since v1.4.0 this module is a compatibility facade over the canonical
pure-Python implementations. It must therefore never auto-discover or execute an
untracked third-party module merely because a package named ``tstdx_native`` is
present in the environment.

The public compatibility names remain available during the deprecation window:
``decode_tdx_float``, ``read_day_file`` and ``parse_kline_payload``. Their
behavior is provided exclusively by repository-owned Python code. ``selftest``
checks those fallback paths against the same canonical implementations used by
the rest of tstdx so CI can verify that the compatibility surface has not
drifted.
"""

from __future__ import annotations

import logging
import struct
import tempfile
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Historical extension name. Kept for diagnostics only; never imported.
_NATIVE_MODULE = "tstdx_native"

#: Representative TDX-float vectors used by the fallback parity diagnostic.
_TDX_FLOAT_VECTORS: tuple[int, ...] = (
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

#: Native execution is intentionally disabled while the extension source is
#: absent from the repository. This is a provenance boundary, not a probe.
NATIVE_AVAILABLE: bool = False
native: Any = None
disable_reason: str | None = (
    "tstdx_native source is retired; repository-owned pure Python is the canonical runtime"
)

warnings.warn(
    "tstdx.native 已弃用（v1.4.0）：Rust 扩展源码已移除，本模块恒为纯 "
    "Python 回退透传，无加速实质；v1.5.0 为迁移最后窗口，v1.6.0 起模块"
    "将被删除。请直接使用 tstdx.codec / tstdx.io 的对应函数。",
    UserWarning,
    stacklevel=2,
)
logger.warning(
    "tstdx.native deprecated (v1.4.0, removed in v1.6.0); "
    "using repository-owned pure-Python compatibility paths"
)


def decode_tdx_float(raw: int) -> float:
    """Decode one TDX custom float through the canonical Python codec."""

    from tstdx.codec.primitive import decode_tdx_float as py_decode

    return py_decode(raw)


def read_day_file(
    path: str | Path,
    *,
    market: int = 1,
    output: str = "dict",
) -> Any:
    """Read one vipdoc ``.day`` file through the canonical Python reader.

    ``market`` remains accepted for source compatibility with the retired native
    signature. The Python reader derives the market/profile from the file path and
    data, as it did for the historical fallback path.
    """

    del market
    from tstdx.reader.formats import read_day_file as py_read

    return py_read(path, output=output)


def parse_kline_payload(
    payload: bytes,
    *,
    category: int,
    price_scale: float = 1000.0,
    lot_factor: int = 0,
    index_mode: bool = False,
) -> Any:
    """Parse a 0x052D K-line payload through the canonical Python parser.

    The compatibility ``lot_factor`` contract is preserved: ``0`` selects the
    parser's category-aware automatic unit, ``1`` means shares, and values at
    least ``100`` select the historical lot (x100) path. Other values are
    rejected instead of being silently ignored.
    """

    from tstdx.codec.primitive import BinaryReader
    from tstdx.protocol.parsers.std7709 import SecurityBarsParser

    factor = int(lot_factor)
    if factor == 0:
        volume_unit = "auto"
    elif factor == 1:
        volume_unit = "share"
    elif factor >= 100:
        volume_unit = "lot"
    else:
        raise NotImplementedError(
            f"Python 回退不支持 lot_factor={factor}（契约：0=auto / 1=share / >=100=lot）"
        )

    return SecurityBarsParser().parse_payload(
        BinaryReader(bytes(payload)),
        category=int(category),
        price_scale=float(price_scale),
        index=bool(index_mode),
        volume_unit=volume_unit,
    )


def _make_day_bytes(n: int = 3) -> bytes:
    """Build a small internally consistent ``.day`` fixture for selftest."""

    record = struct.pack(
        "<IIIIIfII",
        20260831,
        1000,
        1100,
        900,
        1050,
        8_000_000.0,
        800_000,
        1000,
    )
    return record * n


def _make_kline_payload(
    n: int = 2,
    *,
    category: int = 4,
    index: bool = False,
) -> bytes:
    """Build one compact 0x052D payload for fallback parity checks."""

    from tstdx.codec.primitive import encode_leb128
    from tstdx.protocol.parsers.std7709 import DAYLIKE_CATEGORIES

    daylike = category in DAYLIKE_CATEGORIES
    out = bytearray(struct.pack("<H", n))
    for i in range(n):
        if daylike:
            out += struct.pack("<I", 20_260_102 + i)
        else:
            lc16 = (2026 - 2004) * 2048 + 1 * 100 + 2
            out += struct.pack("<HH", lc16, 9 * 60 + 30)
        out += encode_leb128(10_000)
        out += encode_leb128(500)
        out += encode_leb128(1_400)
        out += encode_leb128(-1_000)
        out += struct.pack("<I", 0x4270_0000)
        out += struct.pack("<I", 0x4000_0000)
    if index:
        out += struct.pack("<HH", 1234, 56)
    return bytes(out)


def _rows_equal(got: list[Any], expected: list[Any]) -> bool:
    """Compare parser rows while tolerating insignificant float noise."""

    if len(got) != len(expected):
        return False
    for left, right in zip(got, expected, strict=False):
        for key in ("datetime", "date", "time", "volume", "up_count", "down_count"):
            if left.get(key) != right.get(key):
                return False
        for key in ("open", "close", "high", "low", "amount"):
            if abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) > 1e-6:
                return False
    return True


def selftest() -> dict[str, Any]:
    """Verify the retired compatibility surface against canonical Python code.

    The result keeps the historical ``importable/native_available/disable_reason``
    fields for diagnostics. ``importable`` is deliberately ``False`` because an
    external module is no longer an accepted execution source.
    """

    from tstdx.codec.primitive import BinaryReader
    from tstdx.codec.primitive import decode_tdx_float as py_decode
    from tstdx.protocol.parsers.std7709 import SecurityBarsParser
    from tstdx.reader.formats import read_day_file as py_read

    checks: dict[str, str] = {"native_policy": "ok: retired; external module not loaded"}

    float_ok = all(
        abs(decode_tdx_float(raw) - py_decode(raw)) <= 1e-6
        for raw in _TDX_FLOAT_VECTORS
    )
    checks["decode_tdx_float"] = "ok" if float_ok else "fail: fallback parity"

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sh600000.day"
        path.write_bytes(_make_day_bytes())
        day_ok = read_day_file(path, output="dict") == py_read(path, output="dict")
    checks["read_day_file"] = "ok" if day_ok else "fail: fallback parity"

    kline_ok = True
    scenarios = (
        (4, False),
        (5, False),
        (0, False),
        (4, True),
    )
    for category, index_mode in scenarios:
        payload = _make_kline_payload(category=category, index=index_mode)
        got = list(
            parse_kline_payload(
                payload,
                category=category,
                index_mode=index_mode,
            )
        )
        expected = SecurityBarsParser().parse_payload(
            BinaryReader(payload),
            category=category,
            price_scale=1000.0,
            index=index_mode,
        )
        if not _rows_equal(got, expected):
            kline_ok = False
            break
    checks["parse_kline_payload"] = "ok" if kline_ok else "fail: fallback parity"

    parity_ok = float_ok and day_ok and kline_ok
    return {
        "importable": False,
        "native_available": False,
        "disable_reason": disable_reason,
        "checks": checks,
        "fallback_parity": parity_ok,
    }


__all__ = [
    "native",
    "NATIVE_AVAILABLE",
    "disable_reason",
    "selftest",
    "decode_tdx_float",
    "read_day_file",
    "parse_kline_payload",
]
