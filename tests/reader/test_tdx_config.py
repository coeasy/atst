# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""官方客户端 ``tdxbk.cfg`` / ``tdxhy.cfg`` 只读配置解析测试。

离线用例用合成 GBK 样本覆盖全部分支；真机用例（若本机装了 TdxW.exe）额外核对
官方落盘数据的行数、类型码与市场码，确保解析口径不是对合成样本的过拟合。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atst.errors import DataFileNotFound, FileFormatError
from atst.reader import (
    BOARD_TYPE_NAMES,
    MARKET_PREFIXES,
    BoardConfigReader,
    IndustryConfigReader,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("gbk"))
    return path


class TestBoardConfigReader:
    def test_parses_real_world_layout(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "tdxbk.cfg",
            "1|锂电池|锂电池概念|0\r\n2|科成长层|科成长层风格|\r\n3|MSCIA50|MSCI中国A50|1\r\n",
        )
        assert BoardConfigReader().read(path) == [
            {
                "type": "1",
                "name": "锂电池",
                "full_name": "锂电池概念",
                "flag": "0",
                "kind": "concept",
            },
            {
                "type": "2",
                "name": "科成长层",
                "full_name": "科成长层风格",
                "flag": "",
                "kind": "style",
            },
            {
                "type": "3",
                "name": "MSCIA50",
                "full_name": "MSCI中国A50",
                "flag": "1",
                "kind": "index",
            },
        ]

    def test_blank_lines_skipped_full_name_falls_back_to_name(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "\n1|特斯拉||0\n\n")
        (row,) = BoardConfigReader().read(path)
        assert row["full_name"] == "特斯拉"
        assert row["kind"] == "concept"

    def test_unknown_type_is_reported_not_guessed(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "9|未知|未知板块|0\n")
        with pytest.raises(FileFormatError, match="未知类型"):
            BoardConfigReader().read(path)
        reader = BoardConfigReader(strict=False)
        (row,) = reader.read(path)
        assert row["kind"] == "unknown"
        assert any("未知类型" in w for w in reader.warnings)

    def test_wrong_field_count_strict_and_tolerant(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "1|只有|两列\n")
        with pytest.raises(FileFormatError, match="字段数"):
            BoardConfigReader().read(path)
        reader = BoardConfigReader(strict=False)
        assert reader.read(path) == []
        assert any("字段数" in w for w in reader.warnings)

    def test_empty_short_name_strict_and_tolerant(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "1||空简称|0\n")
        with pytest.raises(FileFormatError, match="简称为空"):
            BoardConfigReader().read(path)
        reader = BoardConfigReader(strict=False)
        assert reader.read(path) == []
        assert any("简称为空" in w for w in reader.warnings)

    def test_output_shapes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "1|锂电池|锂电池概念|0\n")
        reader = BoardConfigReader()
        assert reader.read(path, output="tuple") == [("1", "锂电池", "锂电池概念", "0", "concept")]
        assert reader.read(path, output="dataframe")["name"].tolist() == ["锂电池"]
        assert reader.read(path, output="what") == reader.read(path, output="dict")

    def test_empty_file_and_missing_file(self, tmp_path: Path) -> None:
        empty = _write(tmp_path, "empty.cfg", "")
        assert BoardConfigReader().read(empty) == []
        assert BoardConfigReader().read(empty, output="tuple") == []
        with pytest.raises(DataFileNotFound):
            BoardConfigReader().read(tmp_path / "absent.cfg")

    def test_explicit_encoding(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "1|锂电池|锂电池概念|0\n")
        reader = BoardConfigReader(encoding="gbk")
        assert reader.read(path)[0]["name"] == "锂电池"


class TestIndustryConfigReader:
    def test_market_codes_map_to_canonical_symbols(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "tdxhy.cfg",
            "0|000001|T1001|||X500102\r\n"
            "1|600519|T110201|||X530101\r\n"
            "2|920982|T0403|||X270701\r\n",
        )
        assert IndustryConfigReader().read(path) == [
            {
                "symbol": "sz000001",
                "market": "0",
                "code": "000001",
                "industry": "T1001",
                "sub_industry": "X500102",
            },
            {
                "symbol": "sh600519",
                "market": "1",
                "code": "600519",
                "industry": "T110201",
                "sub_industry": "X530101",
            },
            {
                "symbol": "bj920982",
                "market": "2",
                "code": "920982",
                "industry": "T0403",
                "sub_industry": "X270701",
            },
        ]

    def test_unknown_market_strict_and_tolerant(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "9|600519|T1|||X1\n")
        with pytest.raises(FileFormatError, match="未知市场码"):
            IndustryConfigReader().read(path)
        reader = IndustryConfigReader(strict=False)
        assert reader.read(path) == []
        assert any("未知市场码" in w for w in reader.warnings)

    def test_invalid_code_strict_and_tolerant(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "x.cfg", "0|ABCDEF|T1|||X1\n")
        with pytest.raises(FileFormatError, match="非法代码"):
            IndustryConfigReader().read(path)
        reader = IndustryConfigReader(strict=False)
        assert reader.read(path) == []
        assert any("非法代码" in w for w in reader.warnings)

    def test_wrong_field_count_and_output_shapes(self, tmp_path: Path) -> None:
        bad = _write(tmp_path, "bad.cfg", "0|000001|T1\n")
        reader = IndustryConfigReader(strict=False)
        assert reader.read(bad) == []
        assert any("字段数" in w for w in reader.warnings)

        good = _write(tmp_path, "good.cfg", "0|000001|T1|||X1\n")
        assert reader.read(good, output="tuple") == [("sz000001", "0", "000001", "T1", "X1")]
        assert reader.read(good, output="dataframe")["symbol"].tolist() == ["sz000001"]
        assert reader.read(good, output="what") == reader.read(good)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(DataFileNotFound):
            IndustryConfigReader().read(tmp_path / "absent.cfg")


def test_type_and_market_tables_are_closed_sets() -> None:
    assert BOARD_TYPE_NAMES == {"1": "concept", "2": "style", "3": "index"}
    assert MARKET_PREFIXES == {"0": "sz", "1": "sh", "2": "bj"}


# --- 真机核对：仅当本机装有官方客户端时运行，CI 上 skip ------------------- #
TDX_HQ_CACHE = Path(r"P:\stock\new_tdx64\T0002\hq_cache")


@pytest.mark.skipif(not TDX_HQ_CACHE.exists(), reason="本机未安装 TdxW.exe")
def test_real_client_board_counts() -> None:
    rows = BoardConfigReader().read(TDX_HQ_CACHE / "tdxbk.cfg")
    assert len(rows) == 58
    assert {r["kind"] for r in rows} == {"concept", "style", "index"}
    assert any(r["name"] == "锂电池" and r["kind"] == "concept" for r in rows)
    assert any(r["name"] == "MSCIA50" and r["kind"] == "index" for r in rows)


@pytest.mark.skipif(not TDX_HQ_CACHE.exists(), reason="本机未安装 TdxW.exe")
def test_real_client_industry_counts() -> None:
    rows = IndustryConfigReader().read(TDX_HQ_CACHE / "tdxhy.cfg")
    assert len(rows) == 5670
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["sz000001"]["industry"] == "T1001"
    assert by_symbol["sh600519"]["industry"] == "T030501"
    assert by_symbol["bj920982"]["industry"] == "T0403"
