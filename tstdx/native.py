# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""原生加速内核加载层（C4）。

尝试加载由 pyo3 + maturin 编译的 ``tstdx_native`` Rust 扩展；未安装、
导入失败或**能力自检**不通过时，透明回退纯 Python 实现，功能不缺失。

> **状态注记（v1.4.0）**：**已废弃（M1b 用户决断）**——``tstdx_native/``
> 扩展源码目录已移除，本模块恒为纯 Python 回退（``NATIVE_AVAILABLE`` 恒
> ``False``），无加速实质。按 §29 DeprecationPolicy 自 v1.4.0 起弃用，
> v1.6.0 删除；导入即发 :class:`DeprecationWarning`，窗口期内行为不变。

安全门控
--------
原生函数在模块首次加载时与 Golden 锁定的 Python 参考实现**逐项对拍**：
任一函数输出不一致即整体禁用原生层（``NATIVE_AVAILABLE = False``），
并记录 ``disable_reason``。这样即使 Rust 代码与 Python 语义存在偏差，
也不会把错误结果静默带入业务路径 —— 宁可慢，不可错。

用法
----
::

    from tstdx.native import native, NATIVE_AVAILABLE, selftest, decode_tdx_float

    if NATIVE_AVAILABLE:
        ...  # 原生加速可用

诊断：``selftest()`` 返回逐项对拍结果（供 telemetry / CLI 展示）。
"""

from __future__ import annotations

import importlib
import logging
import struct
import tempfile
import warnings
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 原生扩展模块名（maturin 产物；扩展源码目录已移除，重建时需重新提供
#: pyo3/maturin 构建配置并以本模块的对拍门控验收）
_NATIVE_MODULE = "tstdx_native"

#: tdx_float 对拍向量：覆盖 0 / 边界 / 全 1 / 高位指数等代表性 ``uint32``
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

# --------------------------------------------------------------------------- #
# 状态（模块加载即填充）
# --------------------------------------------------------------------------- #
#: 原生层是否可用（已加载且自检通过）
NATIVE_AVAILABLE: bool = False
#: 原生层不可用的原因（未安装 / 导入失败 / 自检失败明细）
disable_reason: str | None = None
#: 原生扩展模块引用（不可用时为 ``None``）
native: Any = None


# --------------------------------------------------------------------------- #
# 对拍（parity）自检
# --------------------------------------------------------------------------- #
def _compare_tdx_float(mod: Any) -> str | None:
    """decode_tdx_float 对拍：native vs Python 参考实现。"""
    from tstdx.codec.primitive import decode_tdx_float as py_decode

    for raw in _TDX_FLOAT_VECTORS:
        try:
            got = float(mod.decode_tdx_float(raw))
        except Exception as exc:  # noqa: BLE001
            return f"decode_tdx_float(0x{raw:08x}) 抛异常 {type(exc).__name__}: {exc}"
        want = py_decode(raw)
        if abs(got - want) > 1e-6:
            return f"decode_tdx_float(0x{raw:08x}) = {got!r}，Python 参考 = {want!r}（不一致）"
    return None


def _make_day_bytes(n: int = 3) -> bytes:
    """构造 A 股默认 profile 的合成 ``.day`` 内容（与 reader 测试同构）。

    自洽性约束（parity 对拍的前提）：``amount / volume ≈ (high+low)/2``，
    即成交均价须落在 OHLC 区间附近 —— 否则价格缩放自动探测会拿不到高分。
    """
    # u32 日期YYYYMMDD + 4×u32 价格×100 + f32 成交额 + u32 成交量 + u32 昨收
    # 8,000,000 元 / 800,000 股 = 10.0 元 ≈ (11.0 + 9.0) / 2
    rec = struct.pack("<IIIIIfII", 20260831, 1000, 1100, 900, 1050, 8_000_000.0, 800_000, 1000)
    return rec * n


def _compare_day_reader(mod: Any) -> str | None:
    """read_day_file 对拍：native vs Python 参考实现（输出须逐字段一致）。"""
    from tstdx.reader.formats import read_day_file as py_read

    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "sh600000.day")
        Path(path).write_bytes(_make_day_bytes())
        try:
            got = mod.read_day_file(path, 1)
        except Exception as exc:  # noqa: BLE001
            return f"native.read_day_file 抛异常 {type(exc).__name__}: {exc}"
        try:
            want = py_read(path, output="dict")
        except Exception as exc:  # noqa: BLE001
            return f"Python 参考 read_day_file 抛异常 {type(exc).__name__}: {exc}"
        if list(got) != list(want):
            return f"read_day_file 输出不一致: native={got!r} python={want!r}"
    return None


def _make_kline_payload(n: int = 2, *, category: int = 4, index: bool = False) -> bytes:
    """构造 0x052D K 线合成载荷（与 Rust `kline.rs` 测试同构）。

    日线及以上：u32 YYYYMMDD + 4×leb128 差分价格 + u32 tdx_float 量 + u32 tdx_float 额；
    分钟线：u16 lc16 + u16 当日分钟数，其余相同；``index=True`` 尾部追加 u16×2 涨跌家数。
    """
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


def _compare_kline_rows(got: list[Any], want: list[Any]) -> str | None:
    """K 线行逐字段比较（float 用容差，datetime/date/time/volume/计数严格）。"""
    if len(got) != len(want):
        return f"条数不一致: native={len(got)} python={len(want)}"
    for i, (g, w) in enumerate(zip(got, want, strict=False)):
        for key in ("datetime", "date", "time"):
            if g.get(key) != w.get(key):
                return f"row[{i}] {key}: native={g.get(key)!r} python={w.get(key)!r}"
        for key in ("open", "close", "high", "low", "amount"):
            gv, wv = float(g.get(key, 0.0)), float(w.get(key, 0.0))
            if abs(gv - wv) > 1e-6:
                return f"row[{i}] {key}: native={gv!r} python={wv!r}"
        if int(g.get("volume", 0)) != int(w.get("volume", 0)):
            return f"row[{i}] volume: native={g.get('volume')!r} python={w.get('volume')!r}"
        for key in ("up_count", "down_count"):
            if g.get(key) != w.get(key):
                return f"row[{i}] {key}: native={g.get(key)!r} python={w.get(key)!r}"
    return None


def _compare_kline_payload(mod: Any) -> str | None:
    """parse_kline_payload 对拍：native vs Python `SecurityBarsParser`。

    覆盖 日线 / 周线（成交量按「手」×100）/ 分钟线 / 指数（尾部涨跌家数）四种布局。
    """
    from tstdx.codec.primitive import BinaryReader
    from tstdx.protocol.parsers.std7709 import SecurityBarsParser

    scenarios: tuple[dict[str, Any], ...] = (
        {"name": "day_cat4", "category": 4, "index": False},
        {"name": "weekly_cat5_lot", "category": 5, "index": False},
        {"name": "minute_cat0", "category": 0, "index": False},
        {"name": "index_cat4", "category": 4, "index": True},
    )
    for sc in scenarios:
        payload = _make_kline_payload(category=sc["category"], index=sc["index"])
        index_mode = sc["index"]
        try:
            got = list(mod.parse_kline_payload(payload, sc["category"], 1000.0, 0, index_mode))
        except Exception as exc:  # noqa: BLE001
            return f"{sc['name']}: native.parse_kline_payload 抛异常 {type(exc).__name__}: {exc}"
        try:
            want = SecurityBarsParser().parse_payload(
                BinaryReader(payload),
                category=sc["category"],
                price_scale=1000,
                index=index_mode,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                f"{sc['name']}: Python 参考 SecurityBarsParser 抛异常 {type(exc).__name__}: {exc}"
            )
        err = _compare_kline_rows(got, want)
        if err is not None:
            return f"{sc['name']}: {err}"
    return None


#: 原生模块应暴露的顶层函数（用于区分「真扩展」与「源码目录命名空间包」）
_EXPECTED_FUNCS: tuple[str, ...] = ("decode_tdx_float", "read_day_file", "parse_kline_payload")


def _looks_like_extension(mod: Any) -> bool:
    """判断导入到的模块是否为真正的编译扩展。

    仓库根目录下存在 ``tstdx_native/`` 源码目录，Python 会把它当作空的
    命名空间包导入（无任何属性）。若模块连预期函数都没有，视为未安装
    真正的扩展，而不是「自检失败」。
    """
    return all(hasattr(mod, f) for f in _EXPECTED_FUNCS)


def selftest() -> dict[str, Any]:
    """重新运行能力自检，返回结构化结果。

    独立于模块加载时的自动自检，便于诊断与 CI 断言。
    """
    checks: dict[str, Any] = {}
    try:
        mod = importlib.import_module(_NATIVE_MODULE)
    except ImportError:
        return {
            "importable": False,
            "native_available": False,
            "disable_reason": f"{_NATIVE_MODULE} 未安装",
            "checks": {"import": "fail: 扩展未安装"},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "importable": False,
            "native_available": False,
            "disable_reason": f"导入失败: {type(exc).__name__}: {exc}",
            "checks": {"import": f"fail: {exc}"},
        }

    if not _looks_like_extension(mod):
        return {
            "importable": True,
            "native_available": False,
            "disable_reason": f"{_NATIVE_MODULE} 非编译扩展（可能误导入源码目录）",
            "checks": {"import": "warn: 模块无预期函数，非编译扩展"},
        }

    failures: list[str] = []
    for name, fn in (
        ("decode_tdx_float", _compare_tdx_float),
        ("read_day_file", _compare_day_reader),
        ("parse_kline_payload", _compare_kline_payload),
    ):
        err = fn(mod)
        checks[name] = "ok" if err is None else f"fail: {err}"
        if err is not None:
            failures.append(f"{name}: {err}")

    return {
        "importable": True,
        "native_available": not failures,
        "disable_reason": "; ".join(failures) if failures else None,
        "checks": checks,
    }


def _load() -> tuple[Any | None, str | None]:
    """尝试导入原生扩展并跑能力自检。返回 ``(native_module, disable_reason)``。"""
    try:
        mod = importlib.import_module(_NATIVE_MODULE)
    except ImportError:
        return None, f"{_NATIVE_MODULE} 未安装 —— 使用纯 Python 实现"
    except Exception as exc:  # noqa: BLE001
        return None, f"原生扩展导入失败: {type(exc).__name__}: {exc}"

    if not _looks_like_extension(mod):
        return None, f"{_NATIVE_MODULE} 非编译扩展（源码目录被误导入）—— 使用纯 Python 实现"

    failures: list[str] = []
    for _, fn in (
        ("decode_tdx_float", _compare_tdx_float),
        ("read_day_file", _compare_day_reader),
        ("parse_kline_payload", _compare_kline_payload),
    ):
        err = fn(mod)
        if err is not None:
            failures.append(err)
    if failures:
        return None, "能力自检未通过: " + "; ".join(failures)
    return mod, None


# 模块导入即加载（幂等、无副作用；原生未安装时静默回退）
native, disable_reason = _load()
NATIVE_AVAILABLE = native is not None
if NATIVE_AVAILABLE:
    logger.info("tstdx_native 原生加速可用")
elif disable_reason:
    logger.debug("tstdx_native 不可用: %s", disable_reason)

# 深审 M1b 决断（用户拍板，v1.4.0）：废弃本模块——扩展源码已移除、恒为纯
# Python 回退，模块不再承载任何「原生加速」实质。按 §29 DeprecationPolicy
# 走弃用窗口（v1.4.0 起，2 个 minor 后 v1.6.0 删除）；窗口期内功能不变，
# 仅加载时告警。迁移：直接使用 tstdx.codec / tstdx.io 的纯 Python 等价函数
# （本模块的语义化封装自始至终都是对它们的透传）。
#
# P14-D（v1.5.0 强告警）：默认 Python 过滤规则将 DeprecationWarning 隐于
# 用户代码之外（仅 __main__ 可见），窗口期最后版本需**默认可见**才能驱动
# 迁移。自 v1.5.0 起改用 UserWarning（默认显示）+ logging 记录双通道；
# 若项目方希望保留 DeprecationWarning 语义，可在 `python -W` 中显式加回。
warnings.warn(
    "tstdx.native 已弃用（v1.4.0）：Rust 扩展源码已移除，本模块恒为纯 "
    "Python 回退透传，无加速实质；**v1.5.0 为迁移最后窗口，v1.6.0 起模块"
    "将被删除**（导入即 ImportError）。请直接使用 tstdx.codec / tstdx.io "
    "的对应函数（decode_tdx_float / read_day_file / parse_kline_payload）。",
    UserWarning,
    stacklevel=2,
)
logger.warning(
    "tstdx.native deprecated (v1.4.0, removed in v1.6.0). "
    "Please migrate to tstdx.codec / tstdx.io equivalent functions."
)


# --------------------------------------------------------------------------- #
# 语义化封装（native 可用走原生，否则回退 Python 参考实现）
# --------------------------------------------------------------------------- #
def decode_tdx_float(raw: int) -> float:
    """TDX 4 字节自定义浮点 → float。

    语义与 :func:`tstdx.codec.primitive.decode_tdx_float` 完全一致；
    原生层通过自检时使用 Rust 实现，否则回退 Python。
    """
    if native is not None:
        try:
            return float(native.decode_tdx_float(int(raw) & 0xFFFFFFFF))
        except Exception:  # noqa: BLE001 —— 原生异常不阻断，回退 Python
            logger.exception("native.decode_tdx_float 失败，回退 Python")
    from tstdx.codec.primitive import decode_tdx_float as py_decode

    return py_decode(raw)


def read_day_file(path: str | Path, *, market: int = 1, output: str = "dict") -> Any:
    """读取 vipdoc 日线文件（Python 语义三态输出）。

    原生层通过自检（输出与 Python 参考逐字段一致）时走 Rust 加速，
    否则回退 :func:`tstdx.reader.formats.read_day_file`。

    语义边界：原生路径只产出 dict 三态中的 ``dict`` 形态——``output``
    非 ``"dict"``（如 ``"model"`` / ``"tuple"``）时**直接走 Python 回退**，
    保证输出形态与参数一致（此前原生路径会无视 ``output`` 静默返回 dict，
    审计 §3-6）。
    """
    if native is not None and str(output) == "dict":
        try:
            return native.read_day_file(str(path), market)
        except Exception:  # noqa: BLE001
            logger.exception("native.read_day_file 失败，回退 Python")
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
    """批量解析 0x052D K 线载荷。

    输出与 :func:`tstdx.protocol.parsers.std7709.SecurityBarsParser`
    ``parse_payload`` 逐字段一致（``datetime/date/time/open/close/high/low/
    volume/amount/volume_unit``，指数模式含 ``up_count/down_count``）。
    原生层通过自检时走 Rust 加速，否则回退 Python 参考实现。
    """
    if native is not None:
        try:
            return native.parse_kline_payload(
                bytes(payload),
                int(category),
                float(price_scale),
                int(lot_factor),
                bool(index_mode),
            )
        except Exception:  # noqa: BLE001 —— 原生异常不阻断，回退 Python
            logger.exception("native.parse_kline_payload 失败，回退 Python")
    from tstdx.codec.primitive import BinaryReader
    from tstdx.protocol.parsers.std7709 import SecurityBarsParser

    # Python 参考 :meth:`SecurityBarsParser.parse_payload` 的 ctx 没有
    # lot_factor 参数，只有 ``volume_unit``（auto/share/lot）——旧回退路径
    # 直接丢弃 lot_factor，成交量口径与原生层不一致（审计 §3-6）。
    # 本地映射（契约，测试锁定）：0=auto（按 category 推断）、1=share、
    # ≥100=lot（×100）；其余值与原生契约不符 → 显式 NotImplementedError，
    # 不静默吞掉。
    lf = int(lot_factor)
    if lf == 0:
        volume_unit = "auto"
    elif lf == 1:
        volume_unit = "share"
    elif lf >= 100:
        volume_unit = "lot"
    else:
        raise NotImplementedError(
            f"Python 回退不支持 lot_factor={lf}（契约：0=auto / 1=share / ≥100=lot）"
        )
    return SecurityBarsParser().parse_payload(
        BinaryReader(bytes(payload)),
        category=int(category),
        price_scale=price_scale,
        index=index_mode,
        volume_unit=volume_unit,
    )


__all__ = [
    "native",
    "NATIVE_AVAILABLE",
    "disable_reason",
    "selftest",
    "decode_tdx_float",
    "read_day_file",
    "parse_kline_payload",
]
