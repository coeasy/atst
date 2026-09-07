# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""反馈系统：用户统计（Tier B / B1）。

记录用户本地使用统计数据。所有统计操作线程安全，支持随时
:class:`~tstdx.feedback.UserStats.snapshot` 获取当前快照，用于
反馈上报或调试。

设计原则
--------
* **零硬依赖**：仅使用 ``threading`` 与 ``time`` 标准库；
* **线程安全**：所有写操作通过 :class:`threading.Lock` 保护；
* **内存高效**：增量更新，不存储原始事件序列；
* **可重置**：调用 :meth:`reset` 可清空全部累计数据。

典型用法::

    from tstdx.feedback.stats import UserStats

    stats = UserStats()
    stats.record_command("0x0530", 12.5)
    stats.record_error("ConnectionFailed")
    print(stats.snapshot())
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from typing import Any

__all__ = [
    "UserStats",
]


class UserStats:
    """用户本地使用统计。

    跟踪以下维度：

    ==============  ================================================
    属性            含义
    ==============  ================================================
    total_commands  命令总执行次数
    errors_by_type  各错误类型出现次数（:class:`~collections.Counter`）
    latencies       命令耗时列表（仅保留最近 *max_latency_samples* 条）
    start_time      统计窗口起始时间（秒，:func:`time.time`）
    ==============  ================================================

    延迟统计采用 *Welford 在线算法* 的简化版本（增量均值 + 标准差），
    不存储全部历史样本，因此内存占用与时间无关。
    """

    def __init__(self, *, max_latency_samples: int = 10_000) -> None:
        """初始化统计器。

        Parameters
        ----------
        max_latency_samples : int, optional
            内部环形缓冲区保留的最大样本数（仅用于粗略百分位估算）。
            超出后最早的样本会被覆盖。默认 10 000。
        """
        if max_latency_samples <= 0:
            raise ValueError("max_latency_samples 必须为正整数")
        self._lock = threading.RLock()
        self._total_commands: int = 0
        self._errors_by_type: Counter[str] = Counter()
        self._total_errors: int = 0
        self._start_time: float = time.time()

        # 延迟统计（增量均值 + 方差）
        self._latency_count: int = 0
        self._latency_sum: float = 0.0
        self._latency_sum_sq: float = 0.0
        # 环形缓冲区（用于估算百分位，非精确）
        self._latency_samples: list[float] = []
        self._max_latency_samples = max_latency_samples
        self._sample_idx: int = 0  # 环形缓冲写入位置（单调递增，按容量取模）

    # ------------------------------------------------------------------ #
    # 记录
    # ------------------------------------------------------------------ #
    def record_command(self, command: str, duration_ms: float) -> None:
        """记录一次命令执行。

        Parameters
        ----------
        command : str
            命令标识（如 ``"0x0530"``）。
        duration_ms : float
            执行耗时（毫秒）。
        """
        with self._lock:
            self._total_commands += 1
            self._latency_count += 1
            self._latency_sum += duration_ms
            self._latency_sum_sq += duration_ms * duration_ms
            # 环形缓冲写入（修复：此前 else 覆盖分支不可达——idx 取模后恒
            # < 容量，导致 append 分支永远生效、列表无界增长）
            pos = self._sample_idx % self._max_latency_samples
            if len(self._latency_samples) < self._max_latency_samples:
                self._latency_samples.append(duration_ms)
            else:
                self._latency_samples[pos] = duration_ms
            self._sample_idx = pos + 1

    def record_error(self, error_type: str) -> None:
        """记录一次错误。

        Parameters
        ----------
        error_type : str
            异常类名或错误码（如 ``"ConnectionFailed"``、``"E2010"``）。
        """
        with self._lock:
            self._errors_by_type[error_type] += 1
            self._total_errors += 1

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    @property
    def total_commands(self) -> int:
        """命令总执行次数。"""
        with self._lock:
            return self._total_commands

    @property
    def total_errors(self) -> int:
        """错误总次数。"""
        with self._lock:
            return self._total_errors

    @property
    def errors_by_type(self) -> dict[str, int]:
        """各错误类型出现次数（副本）。"""
        with self._lock:
            return dict(self._errors_by_type)

    @property
    def average_latency(self) -> float:
        """命令平均耗时（毫秒）。无样本时返回 0.0。"""
        with self._lock:
            if self._latency_count == 0:
                return 0.0
            return self._latency_sum / self._latency_count

    @property
    def latency_stddev(self) -> float:
        """命令耗时标准差（毫秒）。样本数 < 2 时返回 0.0。"""
        with self._lock:
            n = self._latency_count
            if n < 2:
                return 0.0
            mean = self._latency_sum / n
            # 修正的贝塞尔估计（Bessel's correction）
            var = (self._latency_sum_sq - n * mean * mean) / (n - 1)
            return max(var, 0.0) ** 0.5

    @property
    def commands_per_second(self) -> float:
        """每秒命令速率（基于统计窗口时长）。"""
        with self._lock:
            elapsed = time.time() - self._start_time
            if elapsed <= 0:
                return 0.0
            return self._total_commands / elapsed

    # ------------------------------------------------------------------ #
    # 快照 / 重置
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        """返回当前统计快照（可 JSON 序列化）。

        Returns
        -------
        dict
            包含以下键：

            * ``total_commands`` — int
            * ``total_errors`` — int
            * ``errors_by_type`` — dict[str, int]
            * ``average_latency_ms`` — float
            * ``latency_stddev_ms`` — float
            * ``commands_per_second`` — float
            * ``elapsed_seconds`` — float
        """
        with self._lock:
            elapsed = time.time() - self._start_time
            cps = self._total_commands / elapsed if elapsed > 0 else 0.0
            return {
                "total_commands": self._total_commands,
                "total_errors": self._total_errors,
                "errors_by_type": dict(self._errors_by_type),
                "average_latency_ms": round(self.average_latency, 3),
                "latency_stddev_ms": round(self.latency_stddev, 3),
                "commands_per_second": round(cps, 4),
                "elapsed_seconds": round(elapsed, 3),
            }

    def reset(self) -> None:
        """重置所有统计数据（重新开始计数）。"""
        with self._lock:
            self._total_commands = 0
            self._total_errors = 0
            self._errors_by_type = Counter()
            self._start_time = time.time()
            self._latency_count = 0
            self._latency_sum = 0.0
            self._latency_sum_sq = 0.0
            self._latency_samples = []
            self._sample_idx = 0

    # ------------------------------------------------------------------ #
    # 调试 / 表示
    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:
        with self._lock:
            return (
                f"UserStats(commands={self._total_commands}, "
                f"errors={self._total_errors}, "
                f"avg_latency_ms={self.average_latency:.2f})"
            )
