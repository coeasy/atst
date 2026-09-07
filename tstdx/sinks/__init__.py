# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""兼容 shim：``tstdx.sinks`` 已更名 ``tstdx.output``（v9 Q4-4）。

消除与 :mod:`tstdx.sink`（vipdoc .day 写回）的包名混淆。
本模块将在 v10 删除，请改用 ``tstdx.output``。
"""

from __future__ import annotations

import sys
import warnings

from tstdx.output import (
    Sink,
    duckdb_writer,
    parquet_writer,
    to_csv,
    to_dataframe,
    to_duckdb,
    to_parquet,
    write,
)

__all__ = [
    "Sink",
    "write",
    "to_dataframe",
    "to_parquet",
    "to_csv",
    "to_duckdb",
    "parquet_writer",
    "duckdb_writer",
]

# 子模块路径映射：旧的 tstdx.sinks.xxx 导入路径统一落到 tstdx.output
sys.modules.setdefault("tstdx.sinks", sys.modules[__name__])

warnings.warn(
    "tstdx.sinks 已更名 tstdx.output（v9 Q4-4），本兼容 shim 将在 v10 删除；"
    "请将 import 改为 from tstdx.output import ...",
    DeprecationWarning,
    stacklevel=2,
)
