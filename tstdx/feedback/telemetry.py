# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""反馈系统：遥测事件收集器（Tier B / B1）。

在内存中以环形缓冲区方式收集结构化的遥测事件，支持启用/禁用开关、
定期刷新（:meth:`flush`）到外部目的地（通过回调或传输层），以及
按事件类型过滤查询。

设计原则
--------
* **零硬依赖**：仅使用标准库；
* **默认禁用**：收集器初始为 disabled 状态，需显式调用
  :meth:`enable` 后才记录事件；
* **环形缓冲**：最多保留 *max_size* 条事件，超出后最早的条目被覆盖；
* **线程安全**：所有操作通过 :class:`threading.Lock` 保护。

典型用法::

    from tstdx.feedback.telemetry import TelemetryCollector

    collector = TelemetryCollector(max_size=5000)
    collector.enable()
    collector.record_event("command", {"command": "0x0530", "duration_ms": 12.3})
    collector.record_event("error", {"error_type": "ConnectionFailed"})
    print(collector.events("command"))
    collector.disable()
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

__all__ = [
    "TelemetryCollector",
]


class TelemetryCollector:
    """遥测事件收集器。

    使用环形缓冲区（:class:`collections.deque`）存储最近的事件。
    收集器默认处于 **禁用** 状态——需调用 :meth:`enable` 后
    :meth:`record_event` 才会实际写入；禁用期间记录调用会被静默忽略。

    Parameters
    ----------
    max_size : int, optional
        环形缓冲区最大条目数。默认 1000。
    on_flush : callable, optional
        刷新时的回调函数。签名为 ``(events: list[dict]) -> None``。
        回调抛出的异常不会外溢到调用方。
    """

    def __init__(
        self,
        *,
        max_size: int = 1000,
        on_flush: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        if max_size <= 0:
            raise ValueError("max_size 必须为正整数")
        self._max_size = max_size
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._enabled: bool = False
        self._on_flush = on_flush
        self._total_recorded: int = 0
        self._total_dropped: int = 0  # 超出缓冲区或禁用时丢弃的计数

    # ------------------------------------------------------------------ #
    # 启用 / 禁用
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        """当前是否处于启用状态。"""
        with self._lock:
            return self._enabled

    def enable(self) -> None:
        """启用事件收集。"""
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        """禁用事件收集。后续 :meth:`record_event` 调用将被静默忽略。"""
        with self._lock:
            self._enabled = False

    # ------------------------------------------------------------------ #
    # 记录
    # ------------------------------------------------------------------ #
    def record_event(self, event_type: str, properties: dict[str, Any] | None = None) -> None:
        """记录一条遥测事件。

        Parameters
        ----------
        event_type : str
            事件类型。收集器不校验取值，也不维护标准值表——第 26 轮删掉了那份
            声明了六类却一类都没产出、连本包自己上报的 ``usage`` 都不在表内的
            "标准事件类型"词表（F-81：零执行方的词表按 D3 删除，而不是留成假告示）。
        properties : dict, optional
            事件的附加属性（键值对）。默认空字典。

        禁用期间调用会计入 :attr:`total_dropped`（可观测的静默丢弃），
        不再完全无痕。
        """
        with self._lock:
            if not self._enabled:
                self._total_dropped += 1  # 禁用丢弃计数（可观测）
                return
            event: dict[str, Any] = {
                "timestamp": time.time(),
                "type": event_type,
                "properties": dict(properties or {}),
            }
            if len(self._buffer) >= self._max_size:
                self._total_dropped += 1
            self._buffer.append(event)
            self._total_recorded += 1

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """获取缓冲区内的事件列表（**深拷贝**）。

        Parameters
        ----------
        event_type : str, optional
            若指定，仅返回该类型的事件。

        Returns
        -------
        list[dict]
            按记录顺序排列的事件副本——事件 dict 与内层 ``properties``
            dict 均为拷贝（修复：此前仅浅拷贝，内层 dict 共享引用，
            调用方改写会污染缓冲区）。
        """
        with self._lock:
            if event_type is None:
                selected = list(self._buffer)
            else:
                selected = [e for e in self._buffer if e.get("type") == event_type]
            return [
                {
                    "timestamp": e.get("timestamp"),
                    "type": e.get("type"),
                    "properties": dict(e.get("properties") or {}),
                }
                for e in selected
            ]

    def count(self, event_type: str | None = None) -> int:
        """缓冲区中事件的数量。"""
        with self._lock:
            if event_type is None:
                return len(self._buffer)
            return sum(1 for e in self._buffer if e.get("type") == event_type)

    @property
    def total_recorded(self) -> int:
        """自创建以来成功写入缓冲区的总事件数。"""
        with self._lock:
            return self._total_recorded

    @property
    def total_dropped(self) -> int:
        """被丢弃的事件总数（缓冲区满 + 禁用期间的记录调用）。"""
        with self._lock:
            return self._total_dropped

    @property
    def buffer_size(self) -> int:
        """当前缓冲区中的事件数量。"""
        with self._lock:
            return len(self._buffer)

    # ------------------------------------------------------------------ #
    # 刷新
    # ------------------------------------------------------------------ #
    def flush(self) -> None:
        """将缓冲区内容刷新到外部目的地并清空。

        若配置了 ``on_flush`` 回调，则将所有事件作为列表传递给回调。
        回调抛出的异常被捕获并静默忽略（遥测不应破坏业务路径）。

        刷新后缓冲区被清空，但 :attr:`total_recorded` 和
        :attr:`total_dropped` 保持累计值。
        """
        with self._lock:
            if not self._buffer:
                return
            events_to_flush = list(self._buffer)
            self._buffer.clear()

        if self._on_flush is not None:
            with contextlib.suppress(Exception):  # 遥测回调失败不应外溢
                self._on_flush(events_to_flush)

    def clear(self) -> None:
        """清空缓冲区（不触发刷新回调）。"""
        with self._lock:
            self._buffer.clear()

    # ------------------------------------------------------------------ #
    # 调试 / 表示
    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:
        with self._lock:
            return (
                f"TelemetryCollector(enabled={self._enabled}, "
                f"buffer_size={len(self._buffer)}, "
                f"max_size={self._max_size})"
            )
