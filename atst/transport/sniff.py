# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Sniffer：被动流量采集与未知命令发现（Tier D item D2）。

与 :mod:`atst.protocol.generic.ProtocolSniffer`（在线时自动归档 L2/L3
样本到 ``PROTOCOL_SPEC/_sniffer/``）不同，本模块是**旁路被动观察者**：

* 只在响应到达时记录 ``(cmd_id → payload)`` 样本，**从不主动发请求**；
* 每命令保留最近 N 条 payload（默认 16 条，每条最多 4 KiB）到内存环形缓冲；
* 与 :mod:`atst.protocol.commands` 账本对齐，能自动识别"未登记命令号"；
* 支持把未知命令的观察结果导出为 spec DRAFT yaml，供 :class:`Prober`
  或直接人工评审。

线程安全
--------
所有共享状态受 :class:`threading.RLock` 保护；:meth:`attach` 返回的
wrapper 在响应到达时同步调用 :meth:`Sniffer.observe_response`，
不阻塞业务线程。

与主动探测的分工
----------------
* :mod:`atst.protocol.prober` — 主动发送、**只用于未登记命令**；
* 本模块 — 被动旁路、**可观测所有命令**（含已登记的）；
* 两者共用 :mod:`atst.protocol.commands` 账本作为"已知 vs 未知"的边界。
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
    "detach",
]

#: ``CommandStats.sizes`` 直接构造时的保留窗口（与 :class:`Sniffer` 默认的
#: ``ring_size`` 同值）；由 Sniffer 创建时会改用调用方拧的那个 knob。
_SIZES_WINDOW = 16

#: 同时保留多少个命令号的观察桶（FIFO 淘汰）。每个桶自己有 ``ring_size`` 的窗口，
#: 但**桶的张数**没有上限时，长期嗅探会为每个新命令号永久留一份 payload 原文。
#: 256 远大于任何单次嗅探真正关心的命令数（协议命令账本 85 条）。
MAX_RING_BUCKETS = 256


# --------------------------------------------------------------------------- #
# 结构体
# --------------------------------------------------------------------------- #
@dataclass
class CommandStats:
    """单个命令号的观察统计。"""

    cmd_id: int
    count: int = 0
    #: 最近 ``Sniffer.ring_size`` 次的 payload **原始长度**（与样本环形缓冲同窗口）。
    #: 曾经是 ``list``：每条响应 append 一个整数、永不清理，长期挂着观察的连接
    #: 就此无界增长，``to_dict()``/spec 草稿还会把整份历史原样打印出来（第 26 轮 F-96）。
    #: ``count`` 仍是精确总次数，所以窗口化不掩盖截断。
    sizes: deque[int] = field(default_factory=lambda: deque(maxlen=_SIZES_WINDOW))
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

    留痕的宽度
    ----------
    每个命令号只保留最近 ``ring_size`` 份 payload 样本**和**同样多份长度记录，
    所以 ``size_min``/``size_max``/``size_avg`` 的口径是"最近 ring_size 次"，
    不是全时段；全时段的精确次数在 ``count`` 里。长期挂载不会无界增长。
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
                #: 每命令一张表是**有界的**，但表的张数过去没有上限：cmd_id 理论上
                #: 0..0xFFFF，长期挂在真实连接上嗅探时每遇一个新命令号就新建一份
                #: ``ring_size`` 份 payload 原文并永久保留——与同文件上面已经修好的
                #: "list 永不清理"是同一类缺陷，只是换了个容器。这里按 FIFO 淘汰。
                self._evict_oldest_buckets()
                stats = CommandStats(
                    cmd_id=cmd_id, first_seen=now, sizes=deque(maxlen=self.ring_size)
                )
                self._stats[cmd_id] = stats
                self._rings[cmd_id] = deque(maxlen=self.ring_size)
            stats.count += 1
            stats.sizes.append(size)
            stats.last_seen = now
            stats.last_payload_size = size
            self._rings[cmd_id].append(store)

    def _evict_oldest_buckets(self) -> None:
        """把命令桶的**张数**压到 :data:`MAX_RING_BUCKETS` 以内（调用方须持锁）。``dict``
        的插入序就是观察序，因此弹最前面的即"最久没再出现"的命令。"""
        overflow = len(self._stats) - MAX_RING_BUCKETS
        if overflow <= 0:
            return
        for cmd_id in list(self._stats)[:overflow]:
            self._stats.pop(cmd_id, None)
            self._rings.pop(cmd_id, None)

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

    def _families(self, family: str | None) -> list[str]:
        return [family] if family is not None else list(self.families)

    def known(self, cmd_id: int, family: str | None = None) -> bool:
        """该命令号是否已在 :mod:`atst.protocol.commands` 账本中登记。

        ``family`` 省略时按本观察器的 :attr:`families` 逐族查——只要任一族登记过
        即算已知。写死单一族会把其余族的合法命令号判成未知。
        """
        return any(get_command(cmd_id, fam) is not None for fam in self._families(family))

    def unknown_commands(self, *, family: str | None = None) -> list[int]:
        """所有已观察但**未登记**的命令号，按 cmd_id 升序。

        ``family`` 省略时的判定域同 :meth:`known`（本观察器的全部协议族）。
        """
        with self._lock:
            return sorted(cmd for cmd in self._stats if not self.known(cmd, family=family))

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

        ``family`` 在这里是**双重**参数：既决定草稿归到哪个协议族，也限定
        "未知"的判定域（只查这一族的账本）。因此对一个登记在别的族的命令号，
        本方法会按"本族未知"生成草稿——想要跨族的"全族皆未登记"名单请用
        :meth:`unknown_commands` 的缺省判定域。

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
    :class:`TcpConnection` / :class:`~atst.client.TdxClient` 上，
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
        可选：覆盖 sniffer 观察的协议族集合，即就地改写
        :attr:`Sniffer.families`，随后的 :meth:`Sniffer.known` /
        :meth:`Sniffer.unknown_commands` 缺省判定域随之改变。

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
    if families is not None:
        sniffer.families = list(families)
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
    wrapper._sniffer_owner = sniffer  # type: ignore[attr-defined]
    wrapper.__name__ = getattr(orig, "__name__", "request")
    try:
        target.request = wrapper
    except AttributeError:
        # 不可变对象（如 frozen）；无法就地替换
        return pool_or_client
    return pool_or_client


def detach(sniffer: Sniffer, pool_or_client: Any) -> bool:
    """卸下 :func:`attach` 装上的观察钩子，把目标的 ``request`` 换回原方法。

    与 :func:`attach` 对称：同一个目标解析顺序（先下钻 ``pool``/``_pool``，
    再取对象自身）。attach 是**就地永久替换**，没有这一步就再也拿不回原方法——
    钩子闭包会把整个 Sniffer 连同它的环形缓冲替目标一直留着，直到目标本身被回收
    （第 26 轮 F-95）。

    Parameters
    ----------
    sniffer:
        当初挂上去的那个观察器。目标上同时只有一层钩子，所以这里核对的是
        **归属**：拿另一个观察器来 detach 不会卸下别人的钩子，只返回 ``False``。
    pool_or_client:
        当初 attach 的那个对象。

    Returns
    -------
    ``True`` 说明确有一层钩子被卸下；``False`` 表示目标当前没挂钩子、或钩子不属于
    这个观察器（幂等，可安全重复调用）。
    """
    inner = getattr(pool_or_client, "pool", None) or getattr(pool_or_client, "_pool", None)
    target = inner if inner is not None else pool_or_client
    current = getattr(target, "request", None)
    if not getattr(current, "_sniffer_wrapped", False):
        return False
    if getattr(current, "_sniffer_owner", None) is not sniffer:
        return False
    orig = getattr(current, "__wrapped__", None)
    if orig is None:  # pragma: no cover - attach 必然写入 __wrapped__
        return False
    try:
        target.request = orig
    except AttributeError:  # pragma: no cover - 与 attach 同一不可变分支
        return False
    return True


__all__.append("_hex_of")


def _hex_of(payload: bytes) -> str:
    return payload.hex()
