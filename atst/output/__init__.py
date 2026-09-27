# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""输出层 Sinks（§14）：把内存中的 Bars / Quotes 落盘为多种格式。

统一契约
--------
所有 sink 接收「三态输入」——``list[Bar]`` / ``list[Quote]`` / ``list[dict]``
（即 :func:`atst.client.TdxClient.bars(..., as_format="dict")` 的产出），
输出到：

* **DataFrame** —— 内存分析，需 ``pip install 'atst[dataframe]'``（pandas）。
* **Parquet**  —— 列式持久化，需 ``pyarrow``。
* **DuckDB**   —— 分析型数据库，需 ``duckdb``。

缺依赖时**显式报错**而非静默降级（见 :class:`~atst.errors.DependencyMissingError`）。

v9 自 atst.sinks 更名，消除与 atst.sink（vipdoc .day 写回）的包名混淆。
"""

from __future__ import annotations

import contextlib
import csv
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

#: DuckDB 表名合法字符（白名单校验，防止表名拼接进 SQL 造成注入/语法错误）
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: csv 写出统一 BOM 策略：``utf-8-sig``（带 BOM，Excel 双击直开不乱码）。
#: 与本包其余文本产物保持一致；如需无 BOM 请显式用 to_csv(..., bom=False)。
_CSV_ENCODING = "utf-8-sig"


def _normalize(items: Sequence[Any]) -> list[dict[str, Any]]:
    """把 Bars/Quotes/dict 序列统一成 dict 列表（供各 sink 消费）。"""
    out: list[dict[str, Any]] = []
    for it in items:
        if hasattr(it, "to_dict"):
            out.append(it.to_dict())
        elif isinstance(it, dict):
            out.append(it)
        else:
            out.append(dict(it))
    return out


def to_dataframe(items: Sequence[Any]):
    """转为 pandas DataFrame（需 pandas）。"""
    from ..domain.models import to_dataframe as _td

    return _td(list(items))


def to_parquet(items: Sequence[Any], path: str, *, columns: Sequence[str] | None = None) -> str:
    """写出 Parquet 文件，返回路径。

    Requires
    --------
    ``pyarrow``（``pip install 'atst[parquet]'``）。

    原子性
    ------
    先写同目录临时文件再 :func:`os.replace` 原子替换——半截文件不会被
    下游 reader 观察到（审计 §2-15）；空数据且显式传 ``columns`` 时保留
    列 schema（空表不再是零列）。
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        from ..errors import DependencyMissingError

        raise DependencyMissingError(
            "Parquet 输出需要 pyarrow: pip install 'atst[parquet]'", cause=exc
        ) from exc

    rows = _normalize(items)
    df = to_dataframe(rows) if len(rows) else None
    if df is None or len(df) == 0:
        # 空数据：写一个空表；显式 columns 时保留列 schema（列名不丢）
        if columns is not None:
            table = pa.table({c: pa.array([], type=pa.string()) for c in columns})
        else:
            table = pa.table({})
    else:
        if columns is not None:
            df = df.reindex(columns=list(columns))
        table = pa.Table.from_pandas(df)

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 同目录唯一临时文件（进程号去重），写完原子替换
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent) or "."
    )
    os.close(fd)
    try:
        pq.write_table(table, tmp_name)
        os.replace(tmp_name, str(dest))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return path


def to_csv(
    items: Sequence[Any],
    path: str,
    *,
    columns: Sequence[str] | None = None,
    bom: bool = True,
) -> str:
    """写出 CSV 文件（零依赖，标准库 csv），返回路径。

    BOM 策略
    --------
    默认 ``utf-8-sig``（带 BOM，Excel 双击直开不乱码），``bom=False`` 写纯
    UTF-8。列顺序取 ``columns``（缺省取首行的键序）。

    原子性
    ------
    深审 L10：与 :func:`to_parquet` 同策略——先写同目录唯一临时文件，
    ``os.replace`` 原子替换，写一半崩溃 / 并发读不再暴露残缺文件。
    """
    rows = _normalize(items)
    cols = list(columns) if columns is not None else list(rows[0].keys()) if rows else []
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent) or "."
    )
    os.close(fd)
    try:
        with open(tmp_name, "w", encoding=_CSV_ENCODING if bom else "utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
        os.replace(tmp_name, str(dest))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return path


def to_duckdb(
    items: Sequence[Any],
    table: str,
    *,
    database: str = ":memory:",
    columns: Sequence[str] | None = None,
) -> int:
    """写入 DuckDB 表，返回写入行数。

    Requires
    --------
    ``duckdb``（``pip install 'atst[duckdb]'``）。

    默认 ``database=":memory:"``（临时分析）；传入文件路径即可持久化。
    """
    # 表名校验前置（先于依赖导入）：非法表名无论是否装了 duckdb 都应立即报错
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(
            f"非法 DuckDB 表名: {table!r}（须匹配 ^[A-Za-z_][A-Za-z0-9_]*$，"
            "表名会原样拼入 SQL，禁止引号/分号等字符）"
        )
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        from ..errors import DependencyMissingError

        raise DependencyMissingError(
            "DuckDB 输出需要 duckdb: pip install 'atst[duckdb]'", cause=exc
        ) from exc

    rows = _normalize(items)
    if not rows:
        return 0
    df = to_dataframe(rows)
    if columns is not None:
        df = df.reindex(columns=list(columns))
    con = duckdb.connect(database)
    try:
        con.register("__src", df)
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM __src")  # noqa: S608
        row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
        if row is None:
            raise RuntimeError("DuckDB COUNT 查询未返回结果")
        return int(row[0])
    finally:
        con.close()


class Sink:
    """统一写入入口（策略模式）。

    Examples
    --------
    >>> Sink("parquet", path="out.parquet").write(bars)
    >>> Sink("duckdb", table="kline", database="mkt.duckdb").write(bars)
    """

    def __init__(
        self,
        fmt: str,
        *,
        path: str | None = None,
        table: str | None = None,
        database: str = ":memory:",
        columns: Sequence[str] | None = None,
    ) -> None:
        self.fmt = fmt.lower()
        self.path = path
        self.table = table
        self.database = database
        self.columns = columns

    def write(self, items: Sequence[Any]) -> Any:
        if self.fmt == "dataframe":
            return to_dataframe(items)
        if self.fmt == "parquet":
            if not self.path:
                raise ValueError("Parquet sink 需要 path 参数")
            return to_parquet(items, self.path, columns=self.columns)
        if self.fmt == "duckdb":
            if not self.table:
                raise ValueError("DuckDB sink 需要 table 参数")
            return to_duckdb(items, self.table, database=self.database, columns=self.columns)
        raise ValueError(f"未知 sink 格式: {self.fmt}（可选 dataframe/parquet/duckdb）")


#: 便捷别名
parquet_writer = to_parquet
duckdb_writer = to_duckdb


def write(
    items: Sequence[Any],
    dest: str,
    *,
    fmt: str | None = None,
    columns: Sequence[str] | None = None,
) -> Any:
    """按目标自动选择 sink：``.parquet``/``.pq`` 后缀→Parquet，
    ``duckdb:`` 前缀→DuckDB，``.csv`` 后缀→CSV 文件。

    显式传 ``fmt=``（``"dataframe"`` / ``"parquet"`` / ``"csv"`` / ``"duckdb"``）
    时优先于扩展名推断。**无法从 ``dest`` 推断且未显式传 ``fmt`` 时抛
    :class:`ValueError`**——此前 ``write(bars, "kline.csv")`` 会静默返回
    DataFrame 不落盘（审计 §2-15），该静默语义已移除。

    Examples
    --------
    >>> write(bars, "kline.parquet")
    >>> write(bars, "kline.csv")
    >>> write(bars, "duckdb:mkt.duckdb@kline")
    """
    if fmt is None:
        dest_s = str(dest)
        lower = dest_s.lower()
        if lower.endswith((".parquet", ".pq")):
            fmt = "parquet"
        elif dest_s.startswith("duckdb:"):
            fmt = "duckdb"
        elif lower.endswith(".csv"):
            fmt = "csv"
        else:
            raise ValueError(
                f"无法从目标推断 sink 格式: {dest_s!r}"
                "（支持 .csv/.parquet/.pq/duckdb: 前缀；内存 DataFrame 请显式传 fmt='dataframe'）"
            )
    if fmt == "parquet":
        return to_parquet(items, str(dest), columns=columns)
    if fmt == "csv":
        return to_csv(items, str(dest), columns=columns)
    if fmt == "duckdb":
        # 形如 duckdb:path@table 或 duckdb:table
        spec = str(dest)[len("duckdb:") :]
        if "@" in spec:
            database, table = spec.split("@", 1)
        else:
            database, table = ":memory:", spec
        return to_duckdb(items, table, database=database, columns=columns)
    if fmt == "dataframe":
        return to_dataframe(items)
    raise ValueError(f"未知 sink 格式: {fmt}（可选 dataframe/parquet/csv/duckdb）")
