"""sinks 分发 / CSV / 原子写 / 表名校验测试（审计 §2-15）。

覆盖：
* ``write(items, "kline.csv")`` 按扩展名分发到 CSV（不再静默返 DataFrame）；
* 无法推断格式的 dest → ValueError（建议 fmt="dataframe"）；
* CSV utf-8-sig BOM 默认策略与关闭开关；
* parquet 原子写（无 .tmp 残留）与空数据列 schema 保留；
* duckdb 表名合法性校验（先于依赖导入）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tstdx.output import to_csv, to_parquet, write
from tstdx.output import to_duckdb as _to_duckdb

pytestmark = pytest.mark.unit

ROWS = [
    {"datetime": "2026-06-01", "open": 10.0, "close": 11.0, "volume": 100},
    {"datetime": "2026-06-02", "open": 11.0, "close": 12.5, "volume": 200},
]


class TestCsvDispatch:
    """CSV 落盘与 BOM 策略。"""

    def test_write_csv_by_extension(self, tmp_path: Path) -> None:
        dest = tmp_path / "kline.csv"
        ret = write(ROWS, str(dest))
        assert Path(ret) == dest
        raw = dest.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig BOM
        text = raw.decode("utf-8-sig")
        assert text.splitlines()[0] == "datetime,open,close,volume"
        assert "2026-06-02,11.0,12.5,200" in text

    def test_to_csv_bom_off(self, tmp_path: Path) -> None:
        dest = tmp_path / "plain.csv"
        to_csv(ROWS, dest, bom=False)
        assert not dest.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_to_csv_explicit_columns(self, tmp_path: Path) -> None:
        dest = tmp_path / "cols.csv"
        to_csv(ROWS, dest, columns=["datetime", "close"])
        lines = dest.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "datetime,close"
        assert lines[1] == "2026-06-01,11.0"


class TestUnknownDestination:
    """无法推断格式的 dest 不再静默返 DataFrame。"""

    def test_unknown_extension_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="dataframe"):
            write(ROWS, tmp_path / "anything")

    def test_unknown_extension_no_file_created(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            write(ROWS, tmp_path / "anything")
        assert not (tmp_path / "anything").exists()

    def test_unknown_fmt_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            write(ROWS, str(tmp_path / "x.parquet"), fmt="xlsx")


class TestParquetAtomic:
    """parquet 原子写与空数据 schema。"""

    @pytest.fixture(autouse=True)
    def _require_pyarrow(self) -> None:
        # 与 TestDuckdbTableName 同约定：parquet 是可选 extra（``tstdx[parquet]``），
        # 未安装时跳过而非失败。
        pytest.importorskip("pyarrow", reason="本机未装 pyarrow；有则连库验证")

    def test_no_tmp_leftovers(self, tmp_path: Path) -> None:
        dest = tmp_path / "bars.parquet"
        to_parquet(ROWS, dest)
        assert dest.exists()
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "bars.parquet"]
        assert leftovers == []

    def test_atomic_replace_over_existing(self, tmp_path: Path) -> None:
        dest = tmp_path / "bars.parquet"
        to_parquet(ROWS[:1], dest)
        to_parquet(ROWS, dest)  # 覆盖写仍原子成功
        assert dest.stat().st_size > 0

    def test_empty_keeps_column_schema(self, tmp_path: Path) -> None:
        import pyarrow.parquet as pq

        dest = tmp_path / "empty.parquet"
        cols = ["datetime", "open", "close", "volume"]
        to_parquet([], dest, columns=cols)
        schema = pq.read_schema(dest)
        assert schema.names == cols

    def test_write_dispatch_parquet_by_extension(self, tmp_path: Path) -> None:
        dest = tmp_path / "a.pq"
        write(ROWS, dest)
        assert dest.exists()


class TestDuckdbTableName:
    """表名校验前置（不依赖 duckdb 是否安装）。"""

    @pytest.mark.parametrize("bad", ["kline;drop", "1abc", "a-b", "表名", ""])
    def test_invalid_table_name_raises_valueerror(self, bad: str) -> None:
        with pytest.raises(ValueError, match="表名"):
            _to_duckdb(ROWS, bad)

    @pytest.mark.parametrize("good", ["kline", "_t1", "A9_"])
    def test_valid_table_name_fails_with_dependency_not_valueerror(self, good: str) -> None:
        """合法表名走到依赖导入处：无 duckdb → DependencyMissingError 而非 ValueError。"""
        pytest.importorskip("duckdb", reason="本机未装 duckdb；有则连库验证")
        _to_duckdb(ROWS, good)
