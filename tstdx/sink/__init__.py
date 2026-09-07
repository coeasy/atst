# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""本地落盘层（P1-3 §4）：把网络 K 线增量写回 vipdoc 二进制文件。

与 :mod:`tstdx.output`（DataFrame / Parquet / DuckDB 输出层，v9 前名 tstdx.sinks）不同，
本包写的是**通达信本地格式本身**——目标是让 ``LocalDaySink`` 产出的
``.day`` 文件可直接被 :class:`~tstdx.reader.formats.DayBarReader` 回读，
与本地 vipdoc 数据仓库无缝衔接。
"""

from __future__ import annotations

from .local_day import LocalDaySink, SyncResult, sync_daily  # noqa: F401

__all__ = [
    "LocalDaySink",
    "SyncResult",
    "sync_daily",
]
