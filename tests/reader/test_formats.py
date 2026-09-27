# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""本地 vipdoc 解析器的离线契约：单位换算、时间编码、截断语义、目录规则。

全部用合成字节，不触碰真实行情目录。这些分支此前只被
``tests/reader/test_finance_reader.py`` 的财务一侧覆盖，日线/分钟/板块与
路径拼装整条面处于无保护状态。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atst.errors import DataFileNotFound, TruncatedRecordError
from atst.reader.formats import (
    BaseFileReader,
    BlockReader,
    DayBarReader,
    FinanceReader,
    MinBarReader,
    read_day_file,
    read_min_file,
    resolve_vipdoc_path,
)
from atst.reader.profile import (
    AmountUnit,
    DataProfile,
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
)

pytestmark = pytest.mark.unit

RECORD_SIZE = 32


def day_record(
    date: int = 20260831,
    *,
    open_: int = 1050,
    high: int = 1088,
    low: int = 1040,
    close: int = 1075,
    amount: float = 12345.5,
    volume: int = 20000,
    last: int = 1048,
) -> bytes:
    """A 股 ``.day`` 记录：价格 uint32（×100）、成交额 float32。"""
    return struct.pack("<IIIIIfII", date, open_, high, low, close, amount, volume, last)


def float_day_record(
    date: int = 20260831,
    *,
    open_: float = 10.5,
    high: float = 10.88,
    low: float = 10.4,
    close: float = 10.75,
    amount: float = 12345.5,
    volume: int = 20000,
    last: int = 1048,
) -> bytes:
    """价格 float32 的变体；28-31 仍按文件布局声明为 uint32。"""
    return struct.pack("<IfffffII", date, open_, high, low, close, amount, volume, last)


def min_record(
    lc_date: int = 2048 * 22 + 831,
    *,
    minutes: int = 9 * 60 + 30,
    open_: float = 10.5,
    high: float = 10.88,
    low: float = 10.4,
    close: float = 10.75,
    amount: float = 12345.5,
    volume: int = 200,
) -> bytes:
    return struct.pack("<HHfffffI4x", lc_date, minutes, open_, high, low, close, amount, volume)


def write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


class TestProfileResolution:
    def test_default_profile_is_a_share_day(self) -> None:
        assert DayBarReader().profile.name == "a_share_day"

    def test_builtin_profile_name_is_resolved(self) -> None:
        assert DayBarReader("future_day").profile.extra_fields == ("open_interest",)

    def test_unknown_profile_name_fails_fast(self) -> None:
        with pytest.raises(KeyError, match="未知 profile"):
            DayBarReader("no_such_profile")

    def test_explicit_encoding_overrides_the_profile_charset(self) -> None:
        reader = DayBarReader(encoding="utf-8", auto_detect=False)
        assert reader._prepare(b"") is not None
        assert reader.profile.charset == "gbk"


class TestDayBarReader:
    def test_decodes_uint32_prices_in_yuan_and_shares(self) -> None:
        reader = DayBarReader(auto_detect=False)
        (bar,) = reader.read_bytes(day_record())
        assert bar.datetime == "2026-08-31"
        assert (bar.open, bar.high, bar.low, bar.close) == (10.5, 10.88, 10.4, 10.75)
        assert bar.volume == 20000
        assert bar.amount == pytest.approx(12345.5, abs=0.01)
        assert bar.extra == {"prev_close": 10.48}

    def test_float32_profile_reads_prices_verbatim(self) -> None:
        profile = DataProfile(price_encoding=PriceEncoding.FLOAT32, price_scale=1)
        (bar,) = DayBarReader(profile, auto_detect=False).read_bytes(float_day_record())
        assert (bar.open, bar.close) == (10.5, 10.75)
        # 28-31 的文件布局恒为 uint32：float 价格档案下不做缩放，原样保留
        assert bar.extra == {"prev_close": 1048}
        assert bar.volume == 20000

    def test_future_profile_exposes_open_interest(self) -> None:
        (bar,) = DayBarReader("future_day", auto_detect=False).read_bytes(day_record(last=8888))
        assert bar.extra == {"open_interest": 8888}

    def test_lot_volume_is_normalised_to_shares(self) -> None:
        profile = DataProfile(volume_unit=VolumeUnit.LOT, amount_unit=AmountUnit.WAN)
        (bar,) = DayBarReader(profile, auto_detect=False).read_bytes(day_record(volume=200))
        assert bar.volume == 20000
        assert bar.amount == pytest.approx(123455000.0, rel=1e-4)

    @pytest.mark.parametrize(
        ("encoding", "payload", "expected"),
        [
            (TimeEncoding.YYYYMMDD, day_record(20260831), "2026-08-31"),
            (TimeEncoding.LC16, day_record(2048 * 22 + 831), "2026-08-31"),
            (
                TimeEncoding.DATETIME32,
                day_record((2026 << 20) | (8 << 16) | (31 << 11) | (9 << 6) | 30),
                "2026-08-31 09:30",
            ),
        ],
    )
    def test_time_encodings(self, encoding: str, payload: bytes, expected: str) -> None:
        profile = DataProfile(time_encoding=encoding)
        (bar,) = DayBarReader(profile, auto_detect=False).read_bytes(payload)
        assert bar.datetime == expected

    def test_yyyymmdd_falls_back_to_bitfield_when_out_of_range(self) -> None:
        raw = (2026 << 20) | (8 << 16) | (31 << 11) | (9 << 6) | 30
        (bar,) = DayBarReader(auto_detect=False).read_bytes(day_record(raw))
        assert bar.datetime == "2026-08-31"

    def test_reads_many_records_in_order(self) -> None:
        payload = day_record(20260831) + day_record(20260901, close=1090)
        bars = DayBarReader(auto_detect=False).read_bytes(payload)
        assert [b.datetime for b in bars] == ["2026-08-31", "2026-09-01"]
        assert bars[1].close == 10.9

    def test_strict_mode_rejects_a_partial_tail_record(self, tmp_path: Path) -> None:
        path = write(tmp_path, "sh600519.day", day_record() + b"\x00" * 8)
        with pytest.raises(TruncatedRecordError):
            DayBarReader(auto_detect=False).read(path)

    def test_lenient_mode_truncates_and_warns(self, tmp_path: Path) -> None:
        path = write(tmp_path, "sh600519.day", day_record() + b"\x00" * 8)
        reader = DayBarReader(auto_detect=False, strict=False)
        rows = reader.read(path, output="model")
        assert len(rows) == 1
        assert any("截断" in warning for warning in reader.warnings)

    def test_missing_file_raises_data_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(DataFileNotFound):
            DayBarReader(auto_detect=False).read(tmp_path / "absent.day")

    def test_auto_detect_merges_layout_and_records_low_confidence(self, monkeypatch) -> None:
        calls: dict[str, object] = {}

        def fake_detect(raw: bytes, hint: DataProfile | None = None) -> DataProfile:
            calls["hint"] = hint
            return DataProfile(
                record_size=RECORD_SIZE,
                price_scale=1000,
                price_encoding=PriceEncoding.UINT32,
                volume_unit=VolumeUnit.SHARE,
                time_encoding=TimeEncoding.YYYYMMDD,
                charset="gbk",
                confidence=0.5,
            )

        import atst.reader.formats as formats

        monkeypatch.setattr(formats, "detect_profile", fake_detect)
        reader = DayBarReader()
        (bar,) = reader.read_bytes(day_record())
        assert calls["hint"] is reader.profile
        assert bar.close == 1.075  # 探测出的 ×1000 生效
        assert any("置信度偏低" in warning for warning in reader.warnings)

    def test_auto_detect_failure_degrades_to_the_given_profile(self, monkeypatch) -> None:
        import atst.reader.formats as formats

        def boom(raw: bytes, hint: DataProfile | None = None) -> DataProfile:
            raise RuntimeError("无法探测")

        monkeypatch.setattr(formats, "detect_profile", boom)
        reader = DayBarReader()
        (bar,) = reader.read_bytes(day_record())
        assert bar.close == 10.75
        assert any("自动探测失败" in warning for warning in reader.warnings)


class TestOutputShapes:
    def test_dict_tuple_model_and_dataframe(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x.day", day_record() + day_record(20260901))
        reader = DayBarReader(auto_detect=False)
        dicts = reader.read(path, output="dict")
        assert dicts[0]["datetime"] == "2026-08-31"
        assert dicts[0]["prev_close"] == 10.48  # extra 平铺
        assert isinstance(reader.read(path, output="tuple")[0], tuple)
        assert len(reader.read(path, output="model")) == 2
        frame = reader.read(path, output="dataframe")
        assert list(frame.columns) != [] and len(frame) == 2

    def test_unknown_output_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知输出格式"):
            BaseFileReader._output([], "csv")


class TestMinBarReader:
    def test_float_prices_and_lc16_datetime(self) -> None:
        (bar,) = MinBarReader("a_share_min", auto_detect=False).read_bytes(min_record())
        assert bar.datetime == "2026-08-31 09:30"
        assert (bar.open, bar.close, bar.volume) == (10.5, 10.75, 200)

    def test_integer_price_profile_divides_by_the_scale(self) -> None:
        profile = DataProfile(price_encoding=PriceEncoding.UINT32, price_scale=100)
        raw = struct.pack("<HHIIIIfI4x", 2048 * 22 + 831, 570, 1050, 1088, 1040, 1075, 1234.5, 300)
        (bar,) = MinBarReader(profile, auto_detect=False, interval=5).read_bytes(raw)
        assert bar.datetime == "2026-08-31 09:30"
        assert bar.close == 10.75
        assert bar.amount == pytest.approx(1234.5, abs=0.01)

    def test_reads_from_disk_and_keeps_the_interval(self, tmp_path: Path) -> None:
        path = write(tmp_path, "sh600519.lc1", min_record() * 2)
        reader = MinBarReader("a_share_min", auto_detect=False, interval=1)
        assert len(reader.read(path)) == 2
        assert reader.interval == 1


class TestBlockReader:
    @staticmethod
    def block(name: str, codes: list[str], *, name_len: int = 9) -> bytes:
        encoded = name.encode("gbk")[: name_len - 1].ljust(name_len, b"\x00")
        return (
            encoded
            + struct.pack("<H", len(codes))
            + b"".join(code.encode("gbk").ljust(6, b"\x00") for code in codes)
        )

    def test_flat_layout_dict_output(self, tmp_path: Path) -> None:
        payload = self.block("银行", ["600036", "601398"]) + self.block("证券", ["600030"])
        path = write(tmp_path, "block_gn.dat", payload)
        assert BlockReader(name_length=9).read(path) == [
            {"name": "银行", "count": 2, "codes": ["600036", "601398"]},
            {"name": "证券", "count": 1, "codes": ["600030"]},
        ]

    def test_tuple_and_dataframe_and_model_shapes(self, tmp_path: Path) -> None:
        path = write(tmp_path, "block_zs.dat", self.block("指数", ["000001"]))
        reader = BlockReader(name_length=9)
        assert reader.read(path, output="tuple") == [("指数", ("000001",))]
        frame = reader.read(path, output="dataframe")
        assert frame["block"].tolist() == ["指数"]
        assert reader.read(path, output="model") == [("指数", ["000001"])]

    def test_group_layout_skips_the_two_byte_separator(self, tmp_path: Path) -> None:
        payload = self.block("甲", ["600000"]) + b"\x00\x00" + self.block("乙", ["600001"])
        path = write(tmp_path, "block_custom.dat", payload)
        assert [
            name for name, _ in BlockReader(name_length=9, group=True).read(path, output="model")
        ] == ["甲", "乙"]

    def test_declared_count_beyond_the_payload_is_truncated_with_a_warning(
        self, tmp_path: Path
    ) -> None:
        name = "银行".encode("gbk").ljust(9, b"\x00")
        # 声明 3 个代码却只落 1 个：解析必须收敛到剩余字节并留下告警
        payload = name + struct.pack("<H", 3) + "600036".encode("gbk").ljust(6, b"\x00")
        reader = BlockReader(name_length=9)
        rows = reader.read(write(tmp_path, "x.dat", payload), output="model")
        assert rows == [("银行", ["600036"])]
        assert any("数据不足" in warning for warning in reader.warnings)

    def test_name_length_is_detected_when_not_given(self, tmp_path: Path) -> None:
        path = write(tmp_path, "x.dat", self.block("白酒", ["600519", "000858"]))
        assert BlockReader().read(path) == [
            {"name": "白酒", "count": 2, "codes": ["600519", "000858"]}
        ]


class TestFinanceReader:
    @staticmethod
    def gpcw(values: list[float]) -> bytes:
        return struct.pack(f"<{len(values)}f", *values)

    def test_field_count_is_detected_from_the_file_size(self, tmp_path: Path) -> None:
        path = write(tmp_path, "gpcw20260831.dat", self.gpcw([1.0] * 28) + self.gpcw([2.0] * 28))
        rows = FinanceReader().read(path)
        assert len(rows) == 2
        assert rows[0]["values"][:2] == [1.0, 1.0]

    def test_explicit_field_width_overrides_detection(self, tmp_path: Path) -> None:
        path = write(tmp_path, "gpcw.dat", self.gpcw([float(i) for i in range(30)]))
        (row,) = FinanceReader(fields_per_record=30).read(path, output="tuple")
        assert row == tuple(float(i) for i in range(30))

    def test_irregular_size_is_read_as_a_single_record(self, tmp_path: Path) -> None:
        path = write(tmp_path, "gpcw.dat", self.gpcw([float(i) for i in range(29)]))
        (row,) = FinanceReader().read(path, output="tuple")
        assert len(row) == 29

    def test_indicators_are_named_and_dataframe_convertible(self, tmp_path: Path) -> None:
        payload = self.gpcw([1000.0] + [0.0] * 23 + [5.5, 1.2, 0.3, 2.1][:4] + [0.0] * 0)
        path = write(tmp_path, "gpcw.dat", payload)
        reader = FinanceReader(fields_per_record=28)
        (named,) = reader.read_indicators(path)
        assert named["code"] == "" and named["date"] == ""
        assert named["total_shares"] == 1000.0
        tuples = reader.read_indicators(path, output="tuple")
        assert len(tuples) == 1 and len(tuples[0]) == len(named)
        assert len(reader.read_indicators(path, output="model")) == 1
        assert not reader.read_indicators(path, output="dataframe").empty


class TestVipdocPaths:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("sh600519", "vipdoc/sh/lday/sh600519.day"),
            ("600519.SH", "vipdoc/sh/lday/sh600519.day"),
            ("000001", "vipdoc/sz/lday/sz000001.day"),
            ("bj430047", "vipdoc/bj/lday/bj430047.day"),
        ],
    )
    def test_day_paths_follow_the_client_directory_rule(self, code: str, expected: str) -> None:
        assert resolve_vipdoc_path("vipdoc", code) == Path(expected)

    def test_market_argument_overrides_the_prefix(self) -> None:
        assert resolve_vipdoc_path("v", "000001", market="sh") == Path("v/sh/lday/sh000001.day")

    def test_unparsable_code_keeps_the_legacy_sh_fallback(self) -> None:
        assert resolve_vipdoc_path("v", "not-a-code") == Path("v/sh/lday/shnot-a-code.day")

    def test_minute_periods_use_their_own_directories(self) -> None:
        assert resolve_vipdoc_path("v", "sh600519", Period.M1) == Path("v/sh/minline/sh600519.lc1")
        assert resolve_vipdoc_path("v", "sh600519", Period.M5) == Path("v/sh/fzline/sh600519.lc5")

    def test_periods_without_a_local_format_fail_closed(self) -> None:
        with pytest.raises(ValueError, match="无对应本地文件格式"):
            resolve_vipdoc_path("v", "sh600519", Period.WEEK)


class TestConvenienceFunctions:
    def test_read_day_file_and_min_file_delegate_to_the_readers(self, tmp_path: Path) -> None:
        day = write(tmp_path, "a.day", day_record())
        assert read_day_file(day, profile="a_share_day") == DayBarReader("a_share_day").read(day)
        minute = write(tmp_path, "a.lc5", min_record())
        assert read_min_file(minute, interval=5, profile="a_share_min") == MinBarReader(
            "a_share_min", interval=5
        ).read(minute)
