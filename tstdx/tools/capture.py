# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Golden 样本采集器（§11：样本即契约）。

设计要点
--------
* **自采集**：所有样本由本工具连真实主站抓取，不抄录任何开源项目的样本数据。
* **只落盘原始字节**：一个样本 = ``*.bin``（解压后的 payload）+ ``*.yaml``（元信息）。
  payload 与解析逻辑解耦，解析器改坏了样本也不会失效。
* **可复现**：元信息记录主机、请求体、上下文、抓取时间、payload 的 sha256，
  测试断言时先校验哈希再跑解析，避免"样本被手改过还全绿"。
* **不吞异常**：单个命令失败只记 warning，其余命令继续采集。
* **法律边界**（Tier B B3）：采集前检查交易时段与用户授权，打印法律边界声明。
* **归档**（Tier B B3）：可选 zlib/zstd 压缩归档 + hex 转储 + 结构说明。

目录约定::

    tests/golden/<family>/<command>_<name>/<yyyymmdd-HHMMSS>/
        payload.bin      解压后的响应体（始终为原始字节）
        payload.hex.txt  hex+ascii 三栏转储（可选，默认开启）
        structure.md     帧头字段 + 记录假设（可选，默认开启）
        meta.yaml       元信息
        meta.json       元信息（JSON 格式）

命令行::

    python -m tstdx.tools.capture --host 218.75.126.9:7709 --out tests/golden
    python -m tstdx.tools.capture --plan kline --host ...        # 只抓 K 线族
    python -m tstdx.tools.capture --allow-trading-hours --compressor zlib ...
    python -m tstdx.tools.capture --i-understand-the-legal-boundary ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import struct
import sys
import time
import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..codec.framing import ResponseFrame
from ..domain.calendar import TradingSession, is_trading_day
from ..errors import TdxError
from ..protocol.commands import Family, get_command
from ..transport.base import TcpConnection
from ..transport.hosts import parse_server
from ._console import setup_console
from ._yaml_min import dump_yaml as _yaml_dump

__all__ = [
    "CaptureSpec",
    "CaptureResult",
    "CaptureOptions",
    "PLANS",
    "capture",
    "capture_many",
    "write_sample",
    "main",
    "LEGAL_NOTICE",
    "_shanghai_tz",
]


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
_TOOL_VERSION: str = "0.1.0"
#: --limit 的钳制上限（防止误传大数一口气打爆主站）
_MAX_LIMIT: int = 1000


def _shanghai_tz() -> Any:
    """惰性加载 ``Asia/Shanghai`` 时区（Windows 纯 pip 环境缺 tzdata 时给明确提示）。

    旧实现把 ``ZoneInfo("Asia/Shanghai")`` 放在模块顶层 —— 无 tzdata 的
    Windows 环境连 ``import tstdx.tools.capture`` 都会炸（审计 §2-20）。
    现在推迟到真正需要时区计算（法律自检）时才加载。
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception as exc:  # noqa: BLE001 —— ImportError / ZoneInfoNotFoundError
        raise ImportError(
            "capture 工具需要 tzdata 时区数据库（Windows 纯 pip 环境默认缺失）。"
            "请执行 pip install tzdata 或 pip install 'tstdx[tools]' 后重试。"
        ) from exc


#: 法律边界声明（Tier B B3）
LEGAL_NOTICE: str = (
    "\n"
    "┌──────────────────────────────────────────────────────────────┐\n"
    "│  ⚠️  法律边界声明 (Legal Boundary Notice)                      │\n"
    "│                                                             │\n"
    "│  本工具抓包目标为 TDX 主站协议流量，用途仅限                    │\n"
    "│  互操作性文档 (interoperability documentation)。              │\n"
    "│  用户必须遵守当地法律法规及目标站点服务条款 (ToS)。              │\n"
    "│  采集所得数据不得用于商业转售或未经授权的再分发。                 │\n"
    "│                                                             │\n"
    "│  采集前须确认：                                               │\n"
    "│    1. 当前不在 A 股交易时段 (周一至周五 9:15-11:30, 13:00-15:00)│\n"
    "│       或已设置 --allow-trading-hours                          │\n"
    "│    2. 已设置 --i-understand-the-legal-boundary 标志            │\n"
    "│                                                             │\n"
    "└──────────────────────────────────────────────────────────────┘\n"
)


# --------------------------------------------------------------------------- #
# 采集计划
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CaptureSpec:
    """一条采集指令。"""

    method: int
    #: 请求体（不含 12 字节帧头）
    body: bytes
    #: 解析所需上下文（如 K 线的 category），会一并写入 meta
    ctx: dict[str, Any] = field(default_factory=dict)
    #: 样本别名，用于目录名
    tag: str = ""
    #: 说明
    note: str = ""

    @property
    def name(self) -> str:
        cmd = get_command(self.method, Family.STANDARD)
        base = cmd.name.lower() if cmd else f"cmd_{self.method:04x}"
        return f"{base}_{self.tag}" if self.tag else base

    @property
    def dirname(self) -> str:
        return f"0x{self.method:04x}_{self.name}"


def _bars(market: int, code: str, category: int, count: int = 10, start: int = 0) -> CaptureSpec:
    body = struct.pack(
        "<H6sHHHHIIH",
        market,
        code.encode("ascii"),
        category,
        1,
        start,
        count,
        0,
        0,
        0,
    )
    return CaptureSpec(
        method=0x052D,
        body=body,
        # market/code 入 ctx：golden_audit 的市场维度覆盖与补录指引依赖它
        ctx={"category": category, "market": market, "code": code},
        tag=f"{code}_cat{category}",
        note=f"{code} 周期 {category}，{count} 根",
    )


def _simple(method: int, body: bytes, tag: str, note: str, **ctx: Any) -> CaptureSpec:
    return CaptureSpec(method=method, body=body, ctx=dict(ctx), tag=tag, note=note)


def _plan_kline() -> list[CaptureSpec]:
    """K 线全周期：覆盖两种日期布局 + 两种成交量单位。"""
    out: list[CaptureSpec] = []
    for cat in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        out.append(_bars(1, "600000", cat, count=10))
    # 深市 + 创业板，覆盖市场与价格量级差异
    out.append(_bars(0, "000001", 4, count=10))
    out.append(_bars(0, "300750", 4, count=10))
    return out


def _plan_core() -> list[CaptureSpec]:
    """核心命令：数量 / 行情 / 除权 / 财务 / 分时 / 成交。"""
    out = [
        _simple(0x044E, struct.pack("<H", 0) + b"\x00" * 4, "market0", "深证证券数量", market=0),
        _simple(0x044E, struct.pack("<H", 1) + b"\x00" * 4, "market1", "上证证券数量", market=1),
        _simple(
            0x000F,
            b"600000" + struct.pack("<H", 1),
            "600000",
            "除权除息信息",
            market=1,
            code="600000",
        ),
        _simple(
            0x0010,
            b"600000" + struct.pack("<H", 1),
            "600000",
            "财务基础信息",
            market=1,
            code="600000",
        ),
        _simple(
            0x0537,
            b"600000" + struct.pack("<H", 1) + b"\x00" * 4,
            "600000",
            "分时图数据",
            market=1,
            code="600000",
        ),
        _simple(
            0x0FC5,
            struct.pack("<H6sHH", 1, b"600000", 0, 10),
            "600000",
            "逐笔成交（历史分时成交）",
            market=1,
            code="600000",
        ),
        # --- 深市变体（E8：消除 0x000F/0x0010/0x0537/0x0FC5 的 mkt 单边覆盖）--- #
        _simple(
            0x000F,
            b"000001" + struct.pack("<H", 0),
            "000001",
            "除权除息信息（深市）",
            market=0,
            code="000001",
        ),
        _simple(
            0x0010,
            b"000001" + struct.pack("<H", 0),
            "000001",
            "财务基础信息（深市）",
            market=0,
            code="000001",
        ),
        _simple(
            0x0537,
            b"000001" + struct.pack("<H", 0) + b"\x00" * 4,
            "000001",
            "分时图数据（深市）",
            market=0,
            code="000001",
        ),
        _simple(
            0x0FC5,
            struct.pack("<H6sHH", 0, b"000001", 0, 10),
            "000001",
            "逐笔成交（深市）",
            market=0,
            code="000001",
        ),
    ]
    return out


def _plan_pool() -> list[CaptureSpec]:
    """盘面池命令（E5：D 级在用命令 golden 化前提）。

    0x07E5 板块行情（四类）/ 0x051A 筹码分布 / 0x056A 集合竞价快照。
    采集响应结构即可支撑语义验证与 L1 升级（竞价数据非交易时段
    返回空布局同样具备结构证据价值）。
    """
    out: list[CaptureSpec] = []
    for bt in (0, 1, 2, 3):
        out.append(
            _simple(
                0x07E5,
                struct.pack("<HH", bt, 0),
                f"block_type{bt}",
                f"板块行情（block_type={bt}）",
                block_type=bt,
                start=0,
            )
        )
    out.append(
        _simple(
            0x051A,
            b"600000" + struct.pack("<H", 1) + b"\x00" * 4,
            "600000",
            "量价分布 / 筹码分布",
            market=1,
            code="600000",
        )
    )
    out.append(
        _simple(
            0x056A,
            b"600000" + struct.pack("<H", 1) + b"\x00" * 4,
            "600000",
            "集合竞价过程快照",
            market=1,
            code="600000",
        )
    )
    return out


def _realtime_quote(code: str) -> CaptureSpec:
    """0x0530 实时行情（✅ 当前唯一可用的实时命令，单只）。"""
    from ..protocol.parsers.std7709 import build_realtime_quote_body, infer_market

    market = infer_market(code)
    return CaptureSpec(
        method=0x0530,
        body=build_realtime_quote_body(code),
        ctx={"code": code, "market": market, "price_scale": 100},
        tag=code,
        note=f"{code} 实时快照（market={market}，请求字节已按反转语义换算）",
    )


def _plan_quotes() -> list[CaptureSpec]:
    """实时行情计划。

    .. note::
       ✅ 实测：``0x053E`` / ``0x054C`` / ``0x0532`` / ``0x0450`` 在三台可用主站上
       **全部无响应**（ReadTimeout），判定为该代服务端已下线。因此本计划以
       ``0x0530``（单只实时快照）为主，另保留两条 ``0x053E`` 探测样本用于
       记录「已下线」这一事实（采集会失败并被记账，不影响整体流程）。
    """
    out = [
        _realtime_quote(c)
        for c in (
            "600000",
            "600519",
            "601398",
            "000001",
            "000002",
            "000651",
            "000858",
            "002415",
            "300059",
            "300750",
        )
    ]
    out += [
        _simple(
            0x053E,
            struct.pack("<H", 1) + b"600000" + struct.pack("<H", 1),
            "legacy_1sym",
            "旧版批量行情 0x053E（实测多主站无响应，用于记录下线事实）",
        ),
    ]
    return out


PLANS: dict[str, Callable[[], list[CaptureSpec]]] = {
    "kline": _plan_kline,
    "core": _plan_core,
    "quotes": _plan_quotes,
    "pool": _plan_pool,
    "all": lambda: _plan_kline() + _plan_core() + _plan_quotes() + _plan_pool(),
}


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #
@dataclass
class CaptureResult:
    spec: CaptureSpec
    ok: bool
    host: str
    payload: bytes = b""
    error: str = ""
    elapsed_ms: float = 0.0
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "method": hex(self.spec.method),
            "ok": self.ok,
            "host": self.host,
            "payload_len": len(self.payload),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
            "path": self.path,
        }


@dataclass
class CaptureOptions:
    """采集行为选项（Tier B B3 增强）。"""

    #: 用户确认已理解法律边界
    legal_ack: bool = False
    #: 允许在交易时段采集（仅限维护者）
    allow_trading_hours: bool = False
    #: 压缩器选择: none | zlib | zstd | auto
    compressor: str = "none"
    #: 是否生成 hex 转储 + 结构说明
    dump_hex: bool = True


# --------------------------------------------------------------------------- #
# 法律自检（Tier B B3）
# --------------------------------------------------------------------------- #
def _is_in_trading_hours(
    now: datetime,
) -> bool:
    """``now`` 是否落在 A 股交易相关时段（交易日 + 集合竞价 09:15 至 15:00 CST）。

    时段与交易日均取自 :mod:`tstdx.domain.calendar`：此处曾自带一份只认周末的
    副本，法定节假日会被误判成交易时段而错误中止采集。
    """
    if not is_trading_day(now.date()):
        return False
    total_minutes = now.hour * 60 + now.minute
    if TradingSession.OPEN_AUCTION[0] <= total_minutes < TradingSession.MORNING[1]:
        return True
    return TradingSession.AFTERNOON[0] <= total_minutes < TradingSession.AFTERNOON[1]


def _legal_self_check(
    *,
    now: datetime | None = None,
    allow_trading_hours: bool = False,
    legal_ack: bool = False,
    stdout: Any = sys.stderr,
) -> None:
    """采集前的法律自检（Tier B B3）。

    在任何网络活动之前调用，打印法律边界声明并检查：

    * 当前时间是否在交易时段内 → 除非 ``allow_trading_hours=True``，否则中止
    * 用户是否已确认法律边界 → 除非 ``legal_ack=True``，否则中止

    Parameters
    ----------
    now : datetime | None
        模拟的当前时间（用于测试）。默认使用 Asia/Shanghai 时区真实时间。
    allow_trading_hours : bool
        允许在交易时段内采集（仅维护者）。
    legal_ack : bool
        用户已通过 ``--i-understand-the-legal-boundary`` 确认。
    stdout : Any
        输出流，默认 stderr。

    Raises
    ------
    TdxError
        当自检未通过时抛出，message 包含失败原因。
    """
    tz = _shanghai_tz()
    if now is None:
        now = datetime.now(tz=tz)
    else:
        # 确保有时区信息（用于 weekday/minutes 计算的一致性）
        now = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)

    print(LEGAL_NOTICE, file=stdout)

    in_session = _is_in_trading_hours(now)
    errors: list[str] = []

    if in_session and not allow_trading_hours:
        errors.append(
            f"当前时间 {now.strftime('%Y-%m-%d %H:%M CST')} 在 A 股交易时段内。"
            f"请使用 --allow-trading-hours 或等待收盘后采集。"
        )

    if not legal_ack:
        errors.append(
            "未设置 --i-understand-the-legal-boundary 标志。请阅读法律边界声明后添加此标志以确认。"
        )

    if errors:
        for msg in errors:
            print(f"  ✗  {msg}", file=stdout)
        raise TdxError(
            "法律自检未通过: " + "; ".join(errors),
            context={
                "timestamp": now.isoformat(),
                "in_trading_hours": in_session,
                "allow_trading_hours": allow_trading_hours,
                "legal_ack": legal_ack,
            },
        )

    print(f"  ✓  法律自检通过 ({now.strftime('%Y-%m-%d %H:%M CST')})", file=stdout)


# --------------------------------------------------------------------------- #
# 压缩（Tier B B3）
# --------------------------------------------------------------------------- #
def _compress_payload(
    data: bytes,
    codec: str = "none",
) -> tuple[bytes, str] | None:
    """压缩 payload，返回 (compressed_bytes, codec_name)。

    Parameters
    ----------
    data : bytes
        原始 payload 字节。
    codec : str
        压缩器选择:

        * ``"none"``  — 不压缩，返回 ``None``
        * ``"zlib"``  — zlib level 9
        * ``"zstd"``  — zstandard（需安装），不可用时回退 zlib
        * ``"auto"``  — 优先 zstandard，不可用时回退 zlib

    Returns
    -------
    tuple[bytes, str] | None
        (压缩后字节, 使用的 codec 名称)，或 ``None`` 当 ``codec="none"``。
    """
    if codec == "none":
        return None

    if codec == "zlib":
        return zlib.compress(data, 9), "zlib"

    if codec in ("zstd", "auto"):
        try:
            import zstandard

            cctx = zstandard.ZstdCompressor(level=19)
            return cctx.compress(data), "zstd"
        except ImportError:
            if codec == "zstd":
                print(
                    "  ⚠  zstandard 包不可用，回退 zlib（pip install zstandard 以启用 zstd）",
                    file=sys.stderr,
                )
            return zlib.compress(data, 9), "zlib"

    # unknown codec → 视为 none
    return None


# --------------------------------------------------------------------------- #
# Hex 转储（Tier B B3）
# --------------------------------------------------------------------------- #
_HEX_COLUMNS = 16


def _hex_dump(data: bytes, columns: int = _HEX_COLUMNS) -> str:
    """生成 offset-hex-ascii 三栏转储。

    每行 16 字节，格式::

        00000000  48 65 6c 6c 6f 20 57 6f 72 6c 64 21 0a 00 00 00  |Hello World! ....|

    Parameters
    ----------
    data : bytes
        要转储的字节。
    columns : int
        每行字节数，默认 16。

    Returns
    -------
    str
        格式化的转储文本（含行尾换行）。空数据返回空字符串。
    """
    if not data:
        return ""
    lines: list[str] = []
    for offset in range(0, len(data), columns):
        chunk = data[offset : offset + columns]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        # ASCII 列：可打印字符直接显示，非可打印用 '.'
        ascii_part = "".join(chr(b) if 0x20 <= b < 0x7E else "." for b in chunk)
        # 补全到固定宽度
        hex_width = columns * 3 - 1  # "XX XX ... XX"
        hex_part = hex_part.ljust(hex_width)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 结构分析（Tier B B3）
# --------------------------------------------------------------------------- #
def _infer_record_size(method: int, payload_len: int, ctx: dict[str, Any]) -> tuple[int, str]:
    """根据命令号和 payload 长度推断记录大小。

    Returns
    -------
    tuple[int, str]
        (record_size_hypothesis, confidence_note)
    """
    # 常见记录的已知大小（基于实测样本）；None = 未知，需运行时推断
    known: dict[int, tuple[int | None, str]] = {
        0x0530: (79, "实测 0x0530 单条实时快照 ≈ 79 字节"),
        0x052D: (None, "K 线记录大小随 category 变化，需解析器确认"),
        0x0FC5: (None, "逐笔成交记录大小未知"),
        0x0537: (None, "分时图记录大小未知"),
        0x044E: (None, "证券数量记录大小未知"),
        0x000F: (None, "除权除息记录大小未知"),
        0x0010: (None, "财务基础信息记录大小未知"),
        0x053E: (None, "批量行情（已下线）记录大小未知"),
    }

    entry = known.get(method)
    if entry is not None:
        size, note = entry
        if size is not None and payload_len % size == 0:
            return size, note

    # 通用启发式：尝试常见因子
    for candidate in (79, 56, 32, 30, 28, 16, 8, 4):
        if payload_len % candidate == 0 and payload_len // candidate > 0:
            return candidate, f"启发式推断（payload_len={payload_len}, factor={candidate}）"

    return 0, "无法推断记录大小（payload 长度为 0 或非标准记录）"


def _build_structure_md(
    spec: CaptureSpec,
    frame: ResponseFrame,
    payload: bytes,
) -> str:
    """生成 structure.md 结构说明文件内容。"""
    cmd = get_command(spec.method, Family.STANDARD)
    cmd_name = cmd.name if cmd else "UNKNOWN"
    record_size, size_note = _infer_record_size(spec.method, len(payload), spec.ctx)
    record_count = len(payload) // record_size if record_size > 0 else 0

    # 帧头字段
    header_lines = [
        f"- **magic**: {hex(frame.magic)}",
        f"- **zip_flag**: {frame.zip_flag} ({'compressed' if frame.compressed else 'uncompressed'})",
        f"- **seq**: {frame.seq}",
        f"- **method**: {hex(frame.method)}",
        f"- **zip_size**: {frame.zip_size}",
        f"- **unzip_size**: {frame.unzip_size}",
        f"- **payload_len**: {len(payload)}",
    ]

    # 记录假设
    record_lines = [
        f"- **record_size_hypothesis**: {record_size}",
        f"- **record_count_hypothesis**: {record_count}",
        f"- **size_note**: {size_note}",
    ]

    # 请求体
    req_lines = [
        f"- **method**: {hex(spec.method)}",
        f"- **body_len**: {len(spec.body)}",
        f"- **body_hex**: `{spec.body.hex()}`",
    ]

    # 解析上下文
    ctx_lines = [f"- **{k}**: {v}" for k, v in sorted(spec.ctx.items())]

    return (
        f"# Structure Analysis\n\n"
        f"## Command\n\n"
        f"- **method**: {hex(spec.method)}\n"
        f"- **command_name**: {cmd_name}\n"
        f"- **tag**: {spec.tag}\n"
        f"- **note**: {spec.note}\n\n"
        f"## Frame Header\n\n" + "\n".join(header_lines) + "\n\n"
        "## Record Hypothesis\n\n" + "\n".join(record_lines) + "\n\n"
        "## Request Body\n\n" + "\n".join(req_lines) + "\n\n"
        "## Parse Context\n\n" + ("\n".join(ctx_lines) if ctx_lines else "- (none)") + "\n"
    )


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def capture(
    conn: TcpConnection,
    spec: CaptureSpec,
    *,
    out_dir: Path | None = None,
    timeout: float | None = None,
    options: CaptureOptions | None = None,
) -> CaptureResult:
    """抓一条样本，可选落盘。"""
    started = time.perf_counter()
    host = f"{conn.host}:{conn.port}"
    try:
        frame: ResponseFrame = conn.request(spec.method, spec.body, timeout=timeout)
    except TdxError as exc:
        return CaptureResult(
            spec=spec,
            ok=False,
            host=host,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
    elapsed = (time.perf_counter() - started) * 1000
    result = CaptureResult(spec=spec, ok=True, host=host, payload=frame.payload, elapsed_ms=elapsed)
    if out_dir is not None:
        result.path = str(
            write_sample(out_dir, spec, frame, host, elapsed, options=options or CaptureOptions())
        )
    return result


def capture_many(
    conn: TcpConnection,
    specs: Sequence[CaptureSpec],
    *,
    out_dir: Path | None = None,
    timeout: float | None = None,
    progress: Callable[[CaptureResult], None] | None = None,
    options: CaptureOptions | None = None,
) -> list[CaptureResult]:
    """按序采集；单条失败不影响后续（把 socket 重建交给连接自身）。"""
    results: list[CaptureResult] = []
    opts = options or CaptureOptions()
    for spec in specs:
        res = capture(conn, spec, out_dir=out_dir, timeout=timeout, options=opts)
        results.append(res)
        if progress is not None:
            progress(res)
        # 失败会关闭 socket；下一条 request 会自动重连并重新握手
    return results


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #
# YAML 序列化统一走 tools/_yaml_min.dump_yaml（§3-4 三套 YAML 实现收敛：
# golden meta.yaml / PROTOCOL_SPEC spec / spec_audit 读侧共用同一套
# 零依赖读写器，消除「读侧 _yaml_min + 写侧 capture 私有 dump」的半途态）。
# ``_yaml_dump`` 为历史别名，保留供既有调用点（write_sample）。


def write_sample(
    out_dir: Path,
    spec: CaptureSpec,
    frame: ResponseFrame,
    host: str,
    elapsed_ms: float,
    *,
    options: CaptureOptions | None = None,
) -> Path:
    """写入 ``payload.bin`` + ``meta.yaml`` + ``meta.json``，返回样本目录。

    当 ``options.dump_hex=True`` 时额外生成 ``payload.hex.txt`` 和 ``structure.md``。
    当 ``options.compressor != "none"`` 时额外生成压缩归档文件。
    """
    opts = options or CaptureOptions()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sample_dir = out_dir / Family.STANDARD / spec.dirname / stamp
    sample_dir.mkdir(parents=True, exist_ok=True)

    payload = frame.payload

    # 1) 原始 payload（始终写入，向后兼容）
    (sample_dir / "payload.bin").write_bytes(payload)

    # 2) 压缩归档（可选）
    compressed_data: bytes | None = None
    compression_codec = "none"
    compressed = _compress_payload(payload, opts.compressor)
    if compressed is not None:
        compressed_data, compression_codec = compressed
        archive_name = f"payload.{compression_codec}"
        (sample_dir / archive_name).write_bytes(compressed_data)

    # 3) Hex 转储 + 结构说明（可选，默认开启）
    if opts.dump_hex:
        hex_text = _hex_dump(payload)
        (sample_dir / "payload.hex.txt").write_text(hex_text, encoding="utf-8")

        structure_md = _build_structure_md(spec, frame, payload)
        (sample_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 4) 元信息
    cmd = get_command(spec.method, Family.STANDARD)
    cmd_name = cmd.name if cmd else "UNKNOWN"

    # 尝试获取本机 hostname（仅用于记录）
    try:
        local_host = socket.gethostname()
    except Exception:
        local_host = "unknown"

    # 推断记录大小
    record_size, _size_note = _infer_record_size(spec.method, len(payload), spec.ctx)
    record_count = len(payload) // record_size if record_size > 0 else 0

    # 提取 market/code 从 ctx（如果存在）
    ctx_market = spec.ctx.get("market", "")
    ctx_code = spec.ctx.get("code", "")

    meta = {
        "schema": 2,
        "source": "self-captured",
        "family": Family.STANDARD,
        # --- 命令信息 ---
        "command": hex(spec.method),
        "command_name": cmd_name,
        "tag": spec.tag,
        "note": spec.note,
        # --- 目标与主机 ---
        "host": host,
        "local_host": local_host,
        "market": ctx_market,
        "code": ctx_code,
        # --- 时间信息 ---
        "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        "elapsed_ms": round(elapsed_ms, 2),
        # --- 请求参数 ---
        "request": {
            "method": hex(spec.method),
            "body_hex": spec.body.hex(),
            "body_len": len(spec.body),
        },
        # --- 响应信息 ---
        "response": {
            "zip_size": frame.zip_size,
            "unzip_size": frame.unzip_size,
            "compressed": frame.compressed,
            "payload_len": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        # --- 记录假设 ---
        "record_size_hypothesis": record_size,
        "record_count_hypothesis": record_count,
        # --- 压缩信息 ---
        "compression": {
            "codec": compression_codec,
            "compressed_size": len(compressed_data) if compressed_data is not None else None,
        },
        # --- 解析上下文 ---
        "parse_ctx": spec.ctx,
        # --- 工具信息 ---
        "tool": {
            "name": "tstdx.tools.capture",
            "version": _TOOL_VERSION,
        },
        # --- 法律合规 ---
        "legal": {
            "ack": opts.legal_ack,
            "allow_trading_hours": opts.allow_trading_hours,
        },
    }
    (sample_dir / "meta.yaml").write_text(_yaml_dump(meta), encoding="utf-8")
    (sample_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return sample_dir


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _iter_plans(names: Sequence[str]) -> Iterator[CaptureSpec]:
    for name in names:
        if name not in PLANS:
            raise SystemExit(f"未知采集计划: {name}（可选 {sorted(PLANS)}）")
        yield from PLANS[name]()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。

    契约：TdxError（法律自检未通过 / 主站不可达等）→ stderr 摘要 + exit 2，
    不裸 traceback（审计 §2-20）。
    """
    setup_console()  # Windows GBK 控制台防乱码（UTF-8 + replace）
    parser = argparse.ArgumentParser(
        prog="python -m tstdx.tools.capture",
        description="抓取 TDX 主站响应作为 Golden 回归样本",
    )
    parser.add_argument("--host", default="218.75.126.9:7709", help="主站 host[:port]")
    parser.add_argument("--out", default="tests/golden", help="输出目录")
    parser.add_argument("--plan", action="append", default=None, help="采集计划，可重复；默认 all")
    parser.add_argument("--timeout", type=float, default=3.0, help="读写超时（秒）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出汇总")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验采集计划名并列出条目，不联网（非法计划名在此 raise SystemExit）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本次最多采集的样本条数（钳制到 [0, 1000]）",
    )
    # --- Tier B B3: 法律合规 ---
    parser.add_argument(
        "--i-understand-the-legal-boundary",
        action="store_true",
        help="确认已理解法律边界声明（必填）",
    )
    parser.add_argument(
        "--allow-trading-hours",
        action="store_true",
        help="允许在交易时段内采集（仅维护者）",
    )
    # --- Tier B B3: 压缩 ---
    parser.add_argument(
        "--compressor",
        choices=("none", "zlib", "zstd", "auto"),
        default="none",
        help="压缩归档选择：none（默认）/ zlib / zstd / auto",
    )
    # --- Tier B B3: Hex 转储 ---
    parser.add_argument(
        "--no-dump-hex",
        action="store_true",
        help="禁用 hex 转储和结构说明生成（默认开启）",
    )
    args = parser.parse_args(argv)

    try:
        return _run_capture(args)
    except TdxError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


def _run_capture(args: argparse.Namespace) -> int:
    """capture 主体（main 的网络路径；TdxError 统一由 main 捕获）。"""
    plan_names = args.plan or ["all"]

    # --- --dry-run：只校验 plan 名并列出条目（非法 plan 名由 _iter_plans raise） ---
    if getattr(args, "dry_run", False):
        specs = list(_iter_plans(plan_names))
        limit = getattr(args, "limit", None)
        if limit is not None:
            specs = specs[: max(0, min(int(limit), _MAX_LIMIT))]
        print(f"dry-run: {len(plan_names)} 个计划，共 {len(specs)} 条采集指令（未联网）")
        for s in specs:
            print(f"  - {s.dirname}")
        return 0

    # --- 法律自检（在任何网络活动之前） ---
    options = CaptureOptions(
        legal_ack=args.i_understand_the_legal_boundary,
        allow_trading_hours=args.allow_trading_hours,
        compressor=args.compressor,
        dump_hex=not args.no_dump_hex,
    )
    _legal_self_check(
        allow_trading_hours=options.allow_trading_hours,
        legal_ack=options.legal_ack,
    )

    entry = parse_server(args.host)
    specs = list(_iter_plans(plan_names))
    # --- --limit：上限钳制（防误传 1e9 一口气打爆主站） ---
    limit = getattr(args, "limit", None)
    if limit is not None:
        specs = specs[: max(0, min(int(limit), _MAX_LIMIT))]
    out_dir = Path(args.out)

    conn = TcpConnection(entry.host, entry.port, timeout=args.timeout)
    results: list[CaptureResult] = []
    try:
        conn.connect()
        results = capture_many(
            conn,
            specs,
            out_dir=out_dir,
            timeout=args.timeout,
            progress=lambda r: print(
                f"  {'OK  ' if r.ok else 'FAIL'} {r.spec.dirname:<34} "
                f"{len(r.payload):>7d}B {r.elapsed_ms:6.1f}ms {r.error}",
                file=sys.stderr,
            ),
            options=options,
        )
    finally:
        conn.close()

    ok = sum(1 for r in results if r.ok)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(f"\n采集完成: {ok}/{len(results)} 成功 -> {out_dir}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
