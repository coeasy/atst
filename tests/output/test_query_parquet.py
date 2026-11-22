# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``query_parquet`` 只读查询层测试：只读硬约束 + 跨目录聚合 + 参数绑定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from atst.output import from_parquet, query_parquet, to_parquet

pytestmark = pytest.mark.unit


def _seed(root: Path) -> None:
    """造两份不同类别的 parquet，模拟 data/day/<类别>/<代码>.parquet 布局。"""
    for sub, code, closes in (
        ("stock", "sh600519", [10.0, 11.0, 12.0]),
        ("index", "sh000001", [3000.0, 3010.0]),
    ):
        dest = root / sub / f"{code}.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        to_parquet(
            [{"code": code, "date": f"2026-01-0{i + 1}", "close": c} for i, c in enumerate(closes)],
            str(dest),
        )


def test_query_parquet_aggregates_across_subdirectories(tmp_path: Path) -> None:
    _seed(tmp_path)
    rows = query_parquet(
        tmp_path,
        "SELECT code, COUNT(*) AS n, MAX(close) AS hi FROM bars GROUP BY code ORDER BY code",
    )
    assert [r["code"] for r in rows] == ["sh000001", "sh600519"]
    assert [r["n"] for r in rows] == [2, 3]
    assert rows[1]["hi"] == 12.0


def test_query_parquet_params_binding(tmp_path: Path) -> None:
    _seed(tmp_path)
    rows = query_parquet(
        tmp_path,
        "SELECT date, close FROM bars WHERE code = ? ORDER BY date",
        params=["sh600519"],
    )
    assert len(rows) == 3
    assert rows[0]["date"] == "2026-01-01"


def test_query_parquet_from_parquet_roundtrip(tmp_path: Path) -> None:
    """写出的 parquet 能被只读层读回——落盘 → 查询是完整链路。"""
    dest = tmp_path / "stock" / "sz000001.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    to_parquet([{"code": "sz000001", "date": "2026-02-01", "close": 9.5}], str(dest))
    assert len(from_parquet(str(dest))) == 1
    rows = query_parquet(tmp_path, "SELECT close FROM bars WHERE code = 'sz000001'")
    assert rows[0]["close"] == 9.5


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO bars VALUES (1)",
        "DELETE FROM bars",
        "DROP VIEW bars",
        "CREATE TABLE t AS SELECT 1",
        "PRAGMA version",
        "ATTACH 'x.db' AS x",
        "SELECT 1; DROP TABLE bars",
    ],
)
def test_query_parquet_rejects_non_readonly_sql(tmp_path: Path, sql: str) -> None:
    _seed(tmp_path)
    with pytest.raises(ValueError, match="只读|不允许"):
        query_parquet(tmp_path, sql)


def test_query_parquet_allows_write_word_inside_string_literal(tmp_path: Path) -> None:
    """写关键字出现在字符串字面量里不是写操作——旧的全文子串扫描会误拒合法只读查询。"""
    rows = query_parquet(tmp_path, "SELECT * FROM bars WHERE code = 'drop table'")
    assert rows == []


def test_query_parquet_rejects_multi_statement_bypass(tmp_path: Path) -> None:
    """注释拆开关键字的追加写语句必须被拒——单条语句约束由解析器裁决，不看子串。"""
    with pytest.raises(ValueError, match="只读|SELECT|语句"):
        query_parquet(tmp_path, "SELECT 1; INSERT/**/INTO t VALUES(1)")


def test_query_parquet_rejects_bad_view_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="非法视图名"):
        query_parquet(tmp_path, "SELECT 1", view="bad;name")


def test_query_parquet_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不存在"):
        query_parquet(tmp_path / "nope", "SELECT 1")


def test_query_parquet_empty_dir_returns_empty(tmp_path: Path) -> None:
    rows = query_parquet(tmp_path, "SELECT * FROM bars")
    assert rows == []


def test_query_parquet_root_with_single_quote_is_escaped(tmp_path: Path) -> None:
    """目录名带 ``'`` 时路径字面量必须转义，否则 SQL 被提前截断（或被当成注入）。"""
    root = tmp_path / "O'Brien" / "day"
    _seed(root)
    rows = query_parquet(root, "SELECT COUNT(*) AS n FROM bars")
    assert rows == [{"n": 5}]


def test_query_parquet_root_with_injection_payload_stays_a_literal(tmp_path: Path) -> None:
    """路径里的 ``'; DROP ...`` 只能是一个路径字符，不能变成第二条语句。"""
    root = tmp_path / "x'; DROP TABLE bars; --" / "day"
    _seed(root)
    rows = query_parquet(root, "SELECT COUNT(*) AS n FROM bars")
    assert rows == [{"n": 5}]
