# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Prober：未知 TDX 命令的主动探测（§25 / Tier D item D1）。

本模块为**洁净室协议发现**流程提供主动探测工具。当某个命令号
落在 :mod:`tstdx.protocol.commands` 之外的未知区间时，可用
:class:`Prober` 向主站发送最小请求帧、捕获原始响应字节，
再做结构分析（帧长度、GCD 记录候选、字节直方图、熵、可打印比），
最后把结果写入 :class:`ProbeResult` 与 ``PROTOCOL_SPEC/UNKNOWN/``
下的 DRAFT yaml 供人工评审后升级为正式 spec。

合规与网络压力说明
------------------
本模块的**每一次** :meth:`Prober.probe_command` 都会向真实 TDX 主站发送
一次 TCP 请求。TDX 主站对高频请求敏感：

* **盘中**（09:15–11:30、13:00–15:00 CST 工作日）默认拒绝探测，见
  :meth:`Prober.only_offline_hours`。盘中额外负载可能触发服务端静默断连、
  IP 短时封禁，或对其他用户造成数据延迟；
* **默认速率上限 1 req/s**（:attr:`Prober.rate_limit`），远低于公开限速
  阈值，且通过 :func:`time.monotonic` 在本地强制；
* 每次探测只发一个最小请求体（``market:uint16 + code:char[6]``），
  不重试、不换主站，出错即把原始字节写回 :class:`ProbeResult`；
* 归档产物是**自采集样本**（clean-room artifact），不是从闭源文档拷贝的
  字段布局——这样 DRAFT 里的字段推断有可追溯的证据链，见
  :data:`OFFLINE_HOURS_NOTE`。

用法示例
--------
.. code-block:: python

    from tstdx.transport.pool import ConnectionPool
    from tstdx.protocol.prober import Prober

    pool = ConnectionPool(hosts, family="quotation")
    prober = Prober(client=pool, rate_limit=1.0)
    r = prober.probe_command(0x053E, market=1, code="600519")
    print(r.frame_size, r.plausible_record_size, r.notes)
    path = prober.archive(r)   # → PROTOCOL_SPEC/UNKNOWN/0x053e_DRAFT.yaml

所有网络路径都必须**优雅失败**：超时、连接关闭、协议错误都返回带
``notes`` 的 :class:`ProbeResult`，不抛异常给调用方。
"""

from __future__ import annotations

import math
import struct
import threading
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..errors import TdxError
from ..transport.ratelimit import SessionState, session_state
from .commands import Family

__all__ = [
    "OFFLINE_HOURS_NOTE",
    "ProbeResult",
    "Prober",
]

#: 合规说明（写入每次 DRAFT yaml 头部）。
OFFLINE_HOURS_NOTE: str = (
    "Probing hits real TDX hosts. Trading sessions (09:15–11:30, "
    "13:00–15:00 CST Mon–Fri) are blocked by default; keep the rate limit "
    "at or below 1 req/s and treat every captured sample as clean-room "
    "evidence, not a copy of any closed-source spec."
)

#: 响应帧头长度（见 :mod:`tstdx.codec.framing`）。
_RESPONSE_HEADER_LEN = 16

#: 常见记录长度候选集（供 GCD 分析参考）。
_RECORD_SIZE_CANDIDATES: tuple[int, ...] = (
    1,
    2,
    4,
    6,
    8,
    10,
    12,
    16,
    20,
    22,
    24,
    28,
    32,
    40,
    48,
    64,
)


# --------------------------------------------------------------------------- #
# 结构分析辅助
# --------------------------------------------------------------------------- #
def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _plausible_record_sizes(body_len: int) -> list[int]:
    """推断合理的记录长度候选：整除 body 且长度在常见集内。"""
    if body_len <= 0:
        return []
    hits: list[int] = []
    # 先按候选集命中（优先常见记录长度）
    for rs in _RECORD_SIZE_CANDIDATES:
        if body_len % rs == 0 and body_len // rs >= 1:
            hits.append(rs)
    # 再按 GCD 展开：遍历 body 内若干重复段（如假设 2、4、8 条记录）
    seen = set(hits)
    for n in (2, 4, 8, 16, 32):
        if body_len % n == 0:
            rs = body_len // n
            if rs not in seen and rs in _RECORD_SIZE_CANDIDATES:
                hits.append(rs)
                seen.add(rs)
    # 排序：先按常见性（在 CANDIDATES 中靠前）再按大小
    order = {rs: i for i, rs in enumerate(_RECORD_SIZE_CANDIDATES)}
    hits.sort(key=lambda rs: (order.get(rs, 999), rs))
    return hits


def _byte_stats(raw: bytes) -> tuple[float, float, dict[int, int]]:
    """计算字节直方图、Shannon 熵、可打印字符比例。"""
    if not raw:
        return 0.0, 0.0, {}
    hist: Counter[int] = Counter(raw)
    total = len(raw)
    entropy = 0.0
    for cnt in hist.values():
        p = cnt / total
        entropy -= p * math.log2(p)
    printable = sum(1 for b in raw if 0x20 <= b <= 0x7E)
    return round(entropy, 4), round(printable / total, 4), dict(hist)


# --------------------------------------------------------------------------- #
# ProbeResult
# --------------------------------------------------------------------------- #
@dataclass
class ProbeResult:
    """一次探测的完整结果。

    Attributes
    ----------
    cmd_id:
        命令号（0–0xFFFF）。
    market:
        请求时使用的市场编号（0=深 1=沪 等）。
    code:
        请求时使用的 6 字节代码串。
    raw_response:
        服务端返回的**完整帧**（含 16 字节头）。空字节表示请求失败。
    frame_size:
        ``len(raw_response)``。
    body_size:
        ``frame_size - 16``（去响应头后的 payload 长度）。
    plausible_record_size:
        最可能的记录字节数；``0`` 表示无法推断。
    plausible_record_sizes:
        全部候选，按常见性排序。
    entropy:
        body 的 Shannon 熵（0–8）。
    printable_ratio:
        body 中 ASCII 可打印字符的比例。
    byte_histogram:
        ``{byte_value: count}``，供离线复盘。
    draft_path:
        :meth:`Prober.archive` 生成的 DRAFT yaml 相对路径（若已归档）。
    elapsed_ms:
        请求 + 解析总耗时（毫秒）。
    ok:
        是否拿到了非空的响应帧。
    notes:
        诊断线索（超时原因、协议告警、离线时段拦截等）。
    """

    cmd_id: int
    market: int = 0
    code: str = "000001"
    raw_response: bytes = b""
    frame_size: int = 0
    body_size: int = 0
    plausible_record_size: int = 0
    plausible_record_sizes: list[int] = field(default_factory=list)
    entropy: float = 0.0
    printable_ratio: float = 0.0
    byte_histogram: dict[int, int] = field(default_factory=dict)
    #: 实际发送的请求体（深审 M31：archive 不得重建，自定义 body 的
    #: 复现字节以本字段为准）
    request_body: bytes = b""
    draft_path: str = ""
    elapsed_ms: float = 0.0
    ok: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的 dict（大字段截断）。"""
        d = {
            "cmd_id": f"0x{self.cmd_id:04x}",
            "market": self.market,
            "code": self.code,
            "frame_size": self.frame_size,
            "body_size": self.body_size,
            "plausible_record_size": self.plausible_record_size,
            "plausible_record_sizes": list(self.plausible_record_sizes),
            "entropy": self.entropy,
            "printable_ratio": self.printable_ratio,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "ok": self.ok,
            "notes": list(self.notes),
            "draft_path": self.draft_path,
            "raw_response_hex_head": self.raw_response[:64].hex(),
        }
        return d


# --------------------------------------------------------------------------- #
# Prober
# --------------------------------------------------------------------------- #
class Prober:
    """未知 TDX 命令的主动探测器（Tier D item D1）。

    Parameters
    ----------
    client:
        具备 ``request(method: int, body: bytes, ...) -> ResponseFrame``
        语义的对象，通常是一个 :class:`~tstdx.transport.pool.ConnectionPool`
        或 :class:`~tstdx.transport.base.TcpConnection`。**None 时**探测会
        直接返回带 ``notes=["no client configured"]`` 的 :class:`ProbeResult`，
        便于测试环境做无网络 dry-run。
    rate_limit:
        每秒最多发出的探测请求数；通过 :func:`time.monotonic` 本地强制。
        默认 1.0，建议不要调高。
    archive_dir:
        DRAFT yaml 的落地目录，相对工作目录或绝对路径均可。
    family:
        协议族标签，写入 DRAFT yaml 头部（默认 ``quotation``）。
    timeout:
        单次探测的传输超时（秒），透传给 ``client.request`` 若其支持
        ``timeout=`` 关键字；否则忽略。
    block_offline_only:
        是否在盘中时段拒绝探测。默认 True，见
        :meth:`Prober.only_offline_hours`。
    strict:
        盘中拦截的行为模式。``False``（默认）时不抛异常——返回
        ``ok=False`` 且带说明 ``notes`` 的 :class:`ProbeResult`（与模块
        docstring「所有网络路径优雅失败」对齐）；``True`` 保留旧行为：
        盘中直接抛 :class:`~tstdx.errors.TdxError`。
    """

    def __init__(
        self,
        client: Any = None,
        rate_limit: float = 1.0,
        archive_dir: str = "PROTOCOL_SPEC/UNKNOWN",
        *,
        family: str = Family.STANDARD,
        timeout: float = 3.0,
        block_offline_only: bool = True,
        strict: bool = False,
    ) -> None:
        if float(rate_limit) <= 0:
            raise ValueError(
                f"rate_limit 必须为正数（每秒最大请求数），收到 {rate_limit!r}；"
                "rate_limit<=0 会使 1/rate 的速率等待永无满足（条件性无限阻塞）"
            )
        self.client = client
        self.rate_limit = float(rate_limit)
        self.archive_dir = Path(archive_dir)
        self.family = family
        self.timeout = float(timeout)
        self.block_offline_only = bool(block_offline_only)
        self.strict = bool(strict)
        # 深审 M4：多线程 probe 并发时 _last_probe_monotonic 的读改写需要互斥，
        # 否则限速窗口被并发线程同时满足 → 限速失效。
        self._rate_lock = threading.Lock()
        self._last_probe_monotonic: float = 0.0
        self._total_probes: int = 0
        self._total_failures: int = 0

    # -- 属性 --------------------------------------------------------------- #
    @property
    def total_probes(self) -> int:
        return self._total_probes

    @property
    def total_failures(self) -> int:
        return self._total_failures

    # -- 离线时段守卫 ------------------------------------------------------- #
    def only_offline_hours(self, *, now: datetime | None = None) -> bool:
        """当前时刻是否允许探测（即**非盘中**时段）。

        复用 :func:`tstdx.transport.ratelimit.session_state`，其判定规则为：
        交易日 09:15–11:30 或 13:00–15:00 → ``call_auction`` / ``continuous``
        （拒绝探测），其余时段 → ``noon_break`` / ``closed``（允许探测）。

        Parameters
        ----------
        now:
            可选的探测时刻；None 表示使用当前时间（Asia/Shanghai 时区）。

        Returns
        -------
        True 表示可以探测；False 表示处于盘中时段。
        """
        state = session_state(now)
        return state not in (SessionState.CALL_AUCTION, SessionState.CONTINUOUS)

    def _guard_offline(self) -> None:
        """盘中守卫（**strict 模式**）：处于盘中时段时抛 :class:`TdxError`。

        默认构造（``strict=False``）下 :meth:`probe_command` 不走本方法，
        而是返回带说明 ``notes`` 的失败 :class:`ProbeResult`——与模块
        docstring「不抛异常给调用方」的契约对齐。
        """
        if self.block_offline_only and not self.only_offline_hours():
            raise TdxError(
                "probing blocked during trading hours (09:15–11:30 / 13:00–15:00 CST)",
                context={
                    "note": OFFLINE_HOURS_NOTE,
                    "override": "Prober(block_offline_only=False)",
                },
            )

    # -- 速率限制 ----------------------------------------------------------- #
    def _enforce_rate(self) -> None:
        """阻塞至上一次探测 + 1/rate_limit 秒之后（持锁：并发探测不击穿限速）。"""
        with self._rate_lock:
            if self._last_probe_monotonic > 0:
                dt = time.monotonic() - self._last_probe_monotonic
                need = 1.0 / max(self.rate_limit, 1e-9) - dt
                if need > 0:
                    time.sleep(need)
            self._last_probe_monotonic = time.monotonic()

    # -- 请求体构造 --------------------------------------------------------- #
    @staticmethod
    def _build_probe_body(market: int, code: str) -> bytes:
        """构造最小通用请求体：``market:uint16 + code:char[6]``。

        这是 TDX 7709 标准族最常见的请求体形状（K 线、分时、财务、
        代码表等命令都用这个前缀）。未知命令若不接受该形状，主站会回
        一个错误帧或空 payload——两者都写入 :class:`ProbeResult` 供分析。
        """
        code_b = str(code)[:6].encode("gbk", errors="replace")
        code_b = code_b.ljust(6, b"\x00")
        return struct.pack("<H", market & 0xFFFF) + code_b

    # -- 主入口 ------------------------------------------------------------- #
    def probe_command(
        self,
        cmd_id: int,
        market: int = 0,
        code: str = "000001",
        *,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> ProbeResult:
        """探测单个命令，返回 :class:`ProbeResult`。

        所有失败（超时、连接关闭、协议错误、离线时段拦截）都不抛异常，
        而是把原因写进 ``result.notes`` 并返回。

        Parameters
        ----------
        cmd_id:
            要探测的命令号。
        market, code:
            用于构造默认请求体的市场与代码；忽略时请传入 ``body=``。
        body:
            自定义请求体；给定则忽略 ``market`` / ``code`` 的默认构造。
        timeout:
            单次探测的传输超时；None 使用构造时的 :attr:`Prober.timeout`。
        """
        started = time.perf_counter()
        notes: list[str] = []
        raw: bytes = b""
        ok = False

        # -- 盘中守卫：默认优雅失败（strict=True 才抛异常） ----------------- #
        if self.block_offline_only and not self.only_offline_hours():
            if self.strict:
                self._guard_offline()  # 旧行为：直接抛 TdxError
            blocked_note = (
                "probing blocked during trading hours (09:15–11:30 / 13:00–15:00 CST); "
                "returned failure result without sending any request "
                "(构造 Prober(strict=True) 可恢复盘中抛异常的旧行为)"
            )
            return ProbeResult(
                cmd_id=cmd_id & 0xFFFF,
                market=market,
                code=code,
                raw_response=b"",
                frame_size=0,
                body_size=0,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                ok=False,
                notes=[blocked_note, OFFLINE_HOURS_NOTE],
            )

        self._enforce_rate()
        self._total_probes += 1

        # 默认态：dry-run（无 client）或发送异常时也产出一致的结果对象
        raw = b""
        ok = False
        raw_has_header = False
        actual_request_body = body if body is not None else b""
        if self.client is None:
            notes.append("no client configured; dry-run returned empty result")
        else:
            payload_body = body if body is not None else self._build_probe_body(market, code)
            actual_request_body = payload_body
            effective_timeout = self.timeout if timeout is None else timeout
            try:
                frame = self._send(self.client, cmd_id, payload_body, effective_timeout)
                # 鸭子类型检测：只要有 payload 属性就当 ResponseFrame 用
                if hasattr(frame, "payload"):
                    header = getattr(frame, "header_raw", b"") or b""
                    body_bytes = getattr(frame, "body", b"") or b""
                    if header:
                        # 帧形态：16B 协议头 + 压缩体 → 供下方剥头分析
                        raw = header + body_bytes
                        raw_has_header = True
                    else:
                        # 深审 M4b：无头形态（shim 直接给 payload）——payload
                        # 是否含协议头未知，**不得**无条件再剥 16B（旧实现把
                        # 无头纯 body 误剥 16 字节，结构分析全错位）。
                        raw = frame.payload
                        raw_has_header = False
                    ok = bool(raw)
                else:
                    # 客户端返回原始字节（如某些 shim）——视为完整原始帧
                    raw = bytes(frame)
                    raw_has_header = True
                    ok = bool(raw)
            except Exception as exc:  # noqa: BLE001 - 需要优雅降级
                self._total_failures += 1
                notes.append(f"{type(exc).__name__}: {exc}")

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if raw_has_header and len(raw) > _RESPONSE_HEADER_LEN:
            body_bytes = raw[_RESPONSE_HEADER_LEN:]
        elif raw_has_header:
            body_bytes = b""
        else:
            body_bytes = raw

        # 结构分析
        frame_size = len(raw)
        body_size = len(body_bytes)
        candidates = _plausible_record_sizes(body_size)
        best_rs = candidates[0] if candidates else 0
        entropy, printable, hist = _byte_stats(body_bytes)

        if ok and raw_has_header and frame_size > _RESPONSE_HEADER_LEN:
            # 校验 magic（前 4 字节应为 0x0074CBB1）——仅对含头形态有意义
            magic = struct.unpack("<I", raw[:4])[0] if frame_size >= 4 else 0
            if magic != 0x0074CBB1:
                notes.append(f"magic mismatch: got {magic:#x}")
        elif ok and raw_has_header and frame_size == _RESPONSE_HEADER_LEN:
            notes.append("response has no body (empty payload)")

        result = ProbeResult(
            cmd_id=cmd_id & 0xFFFF,
            market=market,
            code=code,
            raw_response=raw,
            frame_size=frame_size,
            body_size=body_size,
            plausible_record_size=best_rs,
            plausible_record_sizes=candidates,
            entropy=entropy,
            printable_ratio=printable,
            byte_histogram=hist,
            request_body=actual_request_body,
            elapsed_ms=elapsed_ms,
            ok=ok,
            notes=notes,
        )
        return result

    @staticmethod
    def _send(client: Any, cmd_id: int, body: bytes, timeout: float) -> Any:
        """调用 client.request；按签名预判是否接受 timeout 关键字。

        深审 M4c：旧实现用 ``except TypeError`` 重试——客户端内部代码的
        真实 TypeError bug 会被误判为「不支持 timeout」而**二次发送**请求
        （副作用风险 + 掩盖真 bug）。现在用 inspect 预判，TypeError 原样抛出。
        """
        import inspect as _inspect

        try:
            sig = _inspect.signature(client.request)
        except (TypeError, ValueError):
            # 无内省能力的 shim：按宽容路径调用
            try:
                return client.request(cmd_id, body, timeout=timeout)
            except TypeError:
                return client.request(cmd_id, body)
        accepts_timeout = "timeout" in sig.parameters or any(
            p.kind is _inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if accepts_timeout:
            return client.request(cmd_id, body, timeout=timeout)
        return client.request(cmd_id, body)

    def probe_range(
        self,
        start: int,
        end: int,
        *,
        markets: Sequence[int] = (0, 1),
        code: str = "000001",
        step: int = 1,
        max_count: int = 100,
    ) -> list[ProbeResult]:
        """批量探测 ``[start, end]`` 区间内的命令。

        Parameters
        ----------
        start, end:
            命令号闭区间，两端都包含。
        markets:
            对每个命令号依次尝试的市场编号序列；默认沪深。
        code:
            每个请求使用的 6 字节代码。
        step:
            命令号步进（默认 1）。
        max_count:
            最多探测的**命令号**个数（区间宽度钳制，默认 100）。总请求
            次数为 ``min(命令号个数, max_count) × len(markets)``；本方法
            每次调用都会向真实主站发请求，钳制可防 ``probe_range(0, 0xFFFF)``
            这类调用演变成 65536×N 次真实流量。

        Raises
        ------
        ValueError
            ``max_count <= 0``。
        """
        if int(max_count) <= 0:
            raise ValueError(f"max_count 必须为正数（命令号个数上限），收到 {max_count!r}")
        ids = list(range(int(start), int(end) + 1, max(1, int(step))))[: int(max_count)]
        out: list[ProbeResult] = []
        for cmd_id in ids:
            for market in markets:
                out.append(self.probe_command(cmd_id, market=market, code=code))
        return out

    # -- 归档 --------------------------------------------------------------- #
    def archive(self, result: ProbeResult, *, overwrite: bool = False) -> Path:
        """把 :class:`ProbeResult` 写为 ``<archive_dir>/<hex>_DRAFT.yaml``。

        DRAFT 头部包含合规声明、帧统计与结构分析结论；字段布局留空供
        人工补齐。已存在的 DRAFT 默认**不覆盖**（避免覆盖已评审内容）。

        Returns
        -------
        写入的 Path；失败时抛异常。
        """
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        path = self.archive_dir / f"{result.cmd_id:04x}_DRAFT.yaml"
        if path.exists() and not overwrite:
            result.draft_path = str(path)
            return path

        hist_line = (
            ", ".join(f"0x{b:02x}:{c}" for b, c in sorted(result.byte_histogram.items())[:8])
            or "(empty)"
        )
        notes_yaml = "\n".join(f"  - {n!r}" for n in result.notes) or "  []"

        candidates_yaml = (
            str(result.plausible_record_sizes) if result.plausible_record_sizes else "[]"
        )

        yaml_text = f"""# AUTO-GENERATED DRAFT —— 请勿直接提交，需人工补全后升级为正式 spec
# 生成时间: {time.strftime("%Y-%m-%dT%H:%M:%S")}
# 生成方式: tstdx.protocol.prober.Prober.probe_command
# 合规声明: {OFFLINE_HOURS_NOTE}

spec_id: "0x{result.cmd_id:04x}"
name: "unknown_{result.cmd_id:04x}"
family: "{self.family}"
version: "0.1"
description: "auto-drafted probe result — no field layout yet"
status: "draft"
aliases: []

# =============================================================================
# 探测参数
# =============================================================================
probe:
  market: {result.market}
  code: "{result.code}"
  elapsed_ms: {round(result.elapsed_ms, 2)}
  ok: {str(result.ok).lower()}
  notes:
{notes_yaml}

# =============================================================================
# 帧级统计（供人工分析，不做字段推断）
# =============================================================================
frame:
  size: {result.frame_size}
  body_size: {result.body_size}
  plausible_record_size: {result.plausible_record_size}
  plausible_record_sizes: {candidates_yaml}
  entropy: {result.entropy}
  printable_ratio: {result.printable_ratio}
  byte_histogram_head: [{hist_line}]
  raw_head_hex: "{result.raw_response[:64].hex()}"

# =============================================================================
# 请求体（供复现；深审 M31：记录**实际发送**的字节——旧实现 archive 时重建
# 默认 body，调用方传自定义 body 时记录与实际不符）
# =============================================================================
request:
  body_hex: "{result.request_body.hex()}"
  fields: []   # TODO: 人工补全字段布局

# =============================================================================
# 响应体（未确定）
# =============================================================================
response:
  header: []
  fields: []
  record_size: null
  notes: |
    由 Prober 自动推断。请对照原始字节逐字段校验，
    校验通过后删除本文件并新建 <command>.yaml 正式 spec。

notes: |
  本 DRAFT 由 tstdx.protocol.prober.Prober 生成，字段布局待人工补齐。
  所有探测样本均为 clean-room 自采集，不引用任何闭源文档。
"""
        path.write_text(yaml_text, encoding="utf-8")
        result.draft_path = str(path)
        return path


__all__.append("_plausible_record_sizes")  # 供单元测试复用
__all__.append("_byte_stats")
