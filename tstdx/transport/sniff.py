# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Sniffer：被动流量采集与未知命令发现（Tier D item D2）。

与 :mod:`tstdx.protocol.generic.ProtocolSniffer`（在线时自动归档 L2/L3
样本到 ``PROTOCOL_SPEC/_sniffer/``）不同，本模块是**旁路被动观察者**：

* 只在响应到达时记录 ``(cmd_id → payload)`` 样本，**从不主动发请求**；
* 每命令保留最近 N 条 payload（默认 16 条，每条最多 4 KiB）到内存环形缓冲；
* 与 :mod:`tstdx.protocol.commands` 账本对齐，能自动识别"未登记命令号"；
* 支持把未知命令的观察结果导出为 spec DRAFT yaml，供 :class:`Prober`
  或直接人工评审。

线程安全
--------
所有共享状态受 :class:`threading.RLock` 保护；:meth:`attach` 返回的
wrapper 在响应到达时同步调用 :meth:`Sniffer.observe_response`，
不阻塞业务线程。

与主动探测的分工
----------------
* :mod:`tstdx.protocol.prober` — 主动发送、**只用于未登记命令**；
* 本模块 — 被动旁路、**可观测所有命令**（含已登记的）；
* 两者共用 :mod:`tstdx.protocol.commands` 账本作为"已知 vs 未知"的边界。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from ..protocol.commands import Family, get_command

__all__ = [
    "CommandStats",
    "Sniffer",
    "attach",
]


# --------------------------------------------------------------------------- #
# 结构体
# --------------------------------------------------------------------------- #
@dataclass
class CommandStats:
    """单个命令号的观察统计。"""

    cmd_id: int
    count: int = 0
    sizes: list[int] = field(default_factory=list)  # 每次 payload 长度
    first_seen: float = 0.0  # monotonic 秒
    last_seen: float = 0.0
    last_payload_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cmd_id": f"0x{self.cmd_id:04x}",
            "count": self.count,
            "sizes": list(self.sizes),
            "size_min": min(self.sizes) if self.sizes else 0,
            "size_max": max(self.sizes) if self.sizes else 0,
            "size_avg": (sum(self.sizes) / len(self.sizes) if self.sizes else 0.0),
            "first_seen": round(self.first_seen, 3),
            "last_seen": round(self.last_seen, 3),
            "last_payload_size": self.last_payload_size,
        }


# --------------------------------------------------------------------------- #
# Sniffer
# --------------------------------------------------------------------------- #
class Sniffer:
    """被动流量观察器（Tier D item D2）。

    Parameters
    ----------
    ring_size:
        每个命令号环形缓冲保留的最大样本数（默认 16）。
    max_payload_bytes:
        单条 payload 存储上限（默认 4096）。超出的部分被截断并记入
        ``CommandStats.sizes``（记的是**原始长度**，不是截断后长度）。
    families:
        参与"已知 vs 未知"判定的协议族集合。默认全部家族。
    """

    def __init__(
        self,
        *,
        ring_size: int = 16,
        max_payload_bytes: int = 4096,
        families: Sequence[str] | None = None,
    ) -> None:
        if ring_size < 1:
            raise ValueError("ring_size 必须 ≥ 1")
        if max_payload_bytes < 1:
            raise ValueError("max_payload_bytes 必须 ≥ 1")
        self.ring_size = int(ring_size)
        self.max_payload_bytes = int(max_payload_bytes)
        self.families: list[str] = (
            list(families)
            if families is not None
            else [Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10]
        )

        self._stats: dict[int, CommandStats] = {}
        self._rings: dict[int, deque[bytes]] = {}
        self._lock = RLock()

    # -- 观察 --------------------------------------------------------------- #
    def observe_response(self, cmd_id: int, payload: bytes) -> None:
        """记录一次响应。线程安全。

        Parameters
        ----------
        cmd_id:
            命令号（对应 :class:`ResponseFrame.method`）。
        payload:
            响应 payload（对应 :class:`ResponseFrame.payload`）。
            超长会被截断；原始长度进入统计。
        """
        now = time.monotonic()
        size = len(payload)
        store = bytes(payload[: self.max_payload_bytes])

        with self._lock:
            stats = self._stats.get(cmd_id)
            if stats is None:
                stats = CommandStats(cmd_id=cmd_id, first_seen=now)
                self._stats[cmd_id] = stats
                self._rings[cmd_id] = deque(maxlen=self.ring_size)
            stats.count += 1
            stats.sizes.append(size)
            stats.last_seen = now
            stats.last_payload_size = size
            self._rings[cmd_id].append(store)

    def reset(self) -> None:
        """清空所有统计与环形缓冲。"""
        with self._lock:
            self._stats.clear()
            self._rings.clear()

    # -- 查询 --------------------------------------------------------------- #
    def stats(self) -> dict[int, dict[str, Any]]:
        """返回每命令的统计快照（key 为 cmd_id）。"""
        with self._lock:
            return {cmd: s.to_dict() for cmd, s in sorted(self._stats.items())}

    def samples(self, cmd_id: int) -> list[bytes]:
        """返回该命令号环形缓冲中的所有 payload（按时间顺序）。"""
        with self._lock:
            return list(self._rings.get(cmd_id, ()))

    def known(self, cmd_id: int, family: str = Family.STANDARD) -> bool:
        """该命令号是否已在 :mod:`tstdx.protocol.commands` 账本中登记。"""
        return get_command(cmd_id, family) is not None

    def unknown_commands(self, *, family: str = Family.STANDARD) -> list[int]:
        """所有已观察但**未登记**的命令号，按 cmd_id 升序。"""
        with self._lock:
            return sorted(cmd for cmd in self._stats if get_command(cmd, family) is None)

    def observed_commands(self) -> list[int]:
        """所有观察过的命令号（不论是否已知），按 cmd_id 升序。"""
        with self._lock:
            return sorted(self._stats.keys())

    # -- 导出 --------------------------------------------------------------- #
    def export_drafts(
        self,
        out_dir: str = "PROTOCOL_SPEC/UNKNOWN",
        *,
        family: str = Family.STANDARD,
        overwrite: bool = False,
    ) -> list[str]:
        """为所有未知命令号生成 spec DRAFT yaml。

        Returns
        -------
        写入的 yaml 相对路径列表（每个未知命令一个）。
        """
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        unknown = self.unknown_commands(family=family)
        written: list[str] = []

        with self._lock:
            stats_snapshot = {c: s.to_dict() for c, s in self._stats.items()}

        for cmd_id in unknown:
            fname = out_path / f"{cmd_id:04x}_DRAFT.yaml"
            if fname.exists() and not overwrite:
                written.append(str(fname))
                continue
            st = stats_snapshot.get(cmd_id, {})
            sizes = st.get("sizes", [])
            sample_hex_head = ""
            ring = self._rings.get(cmd_id)
            if ring:
                sample_hex_head = ring[-1][:64].hex()
            text = self._render_draft(cmd_id, family, st, sizes, sample_hex_head)
            fname.write_text(text, encoding="utf-8")
            written.append(str(fname))
        return written

    @staticmethod
    def _render_draft(
        cmd_id: int,
        family: str,
        st: dict[str, Any],
        sizes: list[int],
        sample_hex_head: str,
    ) -> str:
        sizes_yaml = str(sizes) if sizes else "[]"
        return f"""\
# AUTO-GENERATED DRAFT —— 由 Sniffer 被动观测生成，需人工评审后升级为正式 spec
# 生成时间: {time.strftime("%Y-%m-%dT%H:%M:%S")}
# 数据来源: 被动流量观测（source: sniffed）
# 合规声明: 本 DRAFT 由 clean-room 自采集样本推断，不引用任何闭源文档。

spec_id: "0x{cmd_id:04x}"
name: "unknown_{cmd_id:04x}"
family: "{family}"
version: "0.1"
description: "auto-drafted from passive sniffing — no field layout yet"
status: "draft"
source: "sniffed"
aliases: []

observation:
  count: {st.get("count", 0)}
  sizes_seen: {sizes_yaml}
  size_min: {st.get("size_min", 0)}
  size_max: {st.get("size_max", 0)}
  size_avg: {round(st.get("size_avg", 0.0), 2)}
  first_seen: {st.get("first_seen", 0.0)}
  last_seen: {st.get("last_seen", 0.0)}
  last_payload_size: {st.get("last_payload_size", 0)}
  sample_hex_head: "{sample_hex_head}"

request:
  fields: []   # TODO: 人工补全请求字段

response:
  header: []
  fields: []
  record_size: null
  notes: |
    由 Sniffer 自动记录。请对照样本逐字段校验，
    校验通过后删除本文件并新建 <command>.yaml 正式 spec。
"""


# --------------------------------------------------------------------------- #
# attach：把 Sniffer 挂到既有客户端 / 连接池
# --------------------------------------------------------------------------- #
def attach(
    sniffer: Sniffer,
    pool_or_client: Any,
    *,
    families: Sequence[str] | None = None,
) -> Any:
    """把 :class:`Sniffer` 挂到既有 :class:`ConnectionPool` /
    :class:`TcpConnection` / :class:`~tstdx.client.TdxClient` 上，
    使每个响应自动进入环形缓冲。

    查找顺序：

    1. ``pool_or_client.pool`` 或 ``pool_or_client._pool``
       （适用于 :class:`TdxClient` 等语义化客户端）；
    2. ``pool_or_client.request``（适用于 :class:`ConnectionPool` 与
       :class:`TcpConnection` 等传输层对象）。

    命中后**就地替换** ``request`` 方法：调用原方法 → 记录响应 →
    返回原返回值。替换是幂等的（重复调用不会层层包裹）。

    Parameters
    ----------
    sniffer:
        要挂上去的观察者。
    pool_or_client:
        目标客户端或连接池。
    families:
        可选：覆盖 sniffer 观察的协议族（当前仅影响未知命令判定，
        实际记录仍按 cmd_id 存）。

    Returns
    -------
    被 attach 的对象（原对象，非 wrapper）。

    Notes
    -----
    * 只观察成功返回的响应；抛异常的调用不会污染环形缓冲。
    * 若目标既无 ``pool``/``_pool`` 属性也无 ``request`` 方法，
      本函数返回该对象但不做任何挂接（调用方需手动调用
      :meth:`Sniffer.observe_response`）。
    """
    # 1) 语义化客户端：优先下钻到内部 pool
    inner = getattr(pool_or_client, "pool", None) or getattr(pool_or_client, "_pool", None)
    target = inner if inner is not None else pool_or_client

    # 2) 检查是否有 request 方法
    orig = getattr(target, "request", None)
    if orig is None or not callable(orig):
        return pool_or_client

    # 幂等：若已挂接，直接返回
    if getattr(orig, "_sniffer_wrapped", False):
        return pool_or_client

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = orig(*args, **kwargs)
        try:
            method = getattr(result, "method", None)
            payload = getattr(result, "payload", None)
            if method is not None and payload is not None:
                sniffer.observe_response(int(method), bytes(payload))
        except Exception:  # pragma: no cover - 观察失败不应影响业务
            pass
        return result

    wrapper._sniffer_wrapped = True  # type: ignore[attr-defined]
    wrapper.__wrapped__ = orig  # type: ignore[attr-defined]
    wrapper.__name__ = getattr(orig, "__name__", "request")
    try:
        target.request = wrapper
    except AttributeError:
        # 不可变对象（如 frozen）；无法就地替换
        return pool_or_client
    return pool_or_client


__all__.append("_hex_of")


def _hex_of(payload: bytes) -> str:
    return payload.hex()
