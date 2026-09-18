"""capture.py 单元测试（Tier B B3 增强）。

覆盖范围：
    * _legal_self_check: 交易时段阻断 + flag 放行
    * _hex_dump: 16 字节行格式与偏移
    * _compress_payload: zlib / none / auto 回退
    * write_sample: metadata 字段完整性
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import zlib
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.errors import TdxError
from tstdx.tools.capture import (
    CaptureOptions,
    CaptureSpec,
    _compress_payload,
    _hex_dump,
    _is_in_trading_hours,
    _legal_self_check,
    write_sample,
)


# --------------------------------------------------------------------------- #
# 测试数据辅助
# --------------------------------------------------------------------------- #
def _make_spec(
    method: int = 0x0530, body: bytes = b"\x01\x00\x36\x30\x30\x30\x30\x30"
) -> CaptureSpec:
    return CaptureSpec(
        method=method,
        body=body,
        ctx={"code": "600000", "market": 1, "price_scale": 100},
        tag="600000",
        note="test sample",
    )


def _make_frame(
    method: int = 0x0530,
    zip_size: int = 79,
    unzip_size: int = 79,
    payload: bytes = b"\x00" * 79,
    seq: int = 0,
) -> ResponseFrame:
    return ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=0 if zip_size == unzip_size else 1,
        seq=seq,
        method=method,
        zip_size=zip_size,
        unzip_size=unzip_size,
        payload=payload,
    )


@pytest.fixture
def tmp_dir():
    """在 workspace 内创建临时目录（避免系统 temp 沙箱限制）。

    .. note::
       旧实现用 ``id(tempfile.NamedTemporaryFile())`` —— CPython 会**复用**
       已释放对象的内存地址，临时文件对象马上被回收后 id 极易重复 →
       不同用例撞到同一目录，teardown 删除又可能被环境守卫拦截，
       引发 ``previous item was not torn down properly`` 级联失败。
       改用 UUID 保证每个用例目录唯一。
    """
    base = Path(__file__).resolve().parents[2] / ".test_tmp"
    d = base / f"capture_test_{uuid4().hex[:12]}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- #
# _is_in_trading_hours
# --------------------------------------------------------------------------- #
class TestTradingHours:
    def test_weekday_morning_session(self):
        # 2026-01-05 is a Monday
        dt = datetime(2026, 1, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_weekday_morning_auction(self):
        dt = datetime(2026, 1, 5, 9, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_weekday_morning_end(self):
        dt = datetime(2026, 1, 5, 11, 29, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_weekday_afternoon(self):
        dt = datetime(2026, 1, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_weekday_afternoon_end(self):
        dt = datetime(2026, 1, 5, 14, 59, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_weekend(self):
        # 2026-01-04 is a Sunday
        dt = datetime(2026, 1, 4, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_before_market(self):
        dt = datetime(2026, 1, 5, 8, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_legal_holiday_weekday(self):
        # 2026-01-02 是周五，但在休市表内：曾经只认周末而误判为交易时段
        dt = datetime(2026, 1, 2, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_lunch_break(self):
        dt = datetime(2026, 1, 5, 12, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_after_close(self):
        dt = datetime(2026, 1, 5, 16, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_before_auction(self):
        dt = datetime(2026, 1, 5, 9, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_exact_morning_start(self):
        dt = datetime(2026, 1, 5, 9, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is True

    def test_exact_morning_end_exclusive(self):
        # 11:30 is the exclusive boundary
        dt = datetime(2026, 1, 5, 11, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False

    def test_exact_afternoon_end_exclusive(self):
        dt = datetime(2026, 1, 5, 15, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _is_in_trading_hours(dt) is False


# --------------------------------------------------------------------------- #
# _legal_self_check
# --------------------------------------------------------------------------- #
class TestLegalSelfCheck:
    def test_blocks_trading_hours_no_flag(self):
        """交易时段内未传 allow_trading_hours → 中止。"""
        dt = datetime(2026, 1, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        stderr = io.StringIO()
        with pytest.raises(TdxError) as exc_info:
            _legal_self_check(
                now=dt,
                allow_trading_hours=False,
                legal_ack=True,
                stdout=stderr,
            )
        assert "交易时段" in exc_info.value.message

    def test_allows_with_trading_hours_flag(self):
        """交易时段内 + allow_trading_hours=True → 通过。"""
        dt = datetime(2026, 1, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        stderr = io.StringIO()
        # 不应抛出
        _legal_self_check(
            now=dt,
            allow_trading_hours=True,
            legal_ack=True,
            stdout=stderr,
        )

    def test_blocks_without_legal_ack(self):
        """非交易时段但未传 legal_ack → 中止。"""
        dt = datetime(2026, 1, 4, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Sunday
        with pytest.raises(TdxError) as exc_info:
            _legal_self_check(
                now=dt,
                allow_trading_hours=False,
                legal_ack=False,
                stdout=io.StringIO(),
            )
        msg = exc_info.value.message
        assert "legal" in msg.lower() or "legal" in str(exc_info.value.context).lower()

    def test_allows_when_not_trading_hours_with_ack(self):
        """非交易时段 + legal_ack=True → 通过。"""
        dt = datetime(2026, 1, 4, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # Sunday
        # 不应抛出
        _legal_self_check(
            now=dt,
            allow_trading_hours=False,
            legal_ack=True,
            stdout=io.StringIO(),
        )

    def test_blocks_both_conditions(self):
        """交易时段 + 无 legal_ack + 无 allow_trading_hours → 两条错误都报告。"""
        dt = datetime(2026, 1, 5, 14, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        stderr = io.StringIO()
        with pytest.raises(TdxError) as exc_info:
            _legal_self_check(
                now=dt,
                allow_trading_hours=False,
                legal_ack=False,
                stdout=stderr,
            )
        msg = exc_info.value.message
        assert "交易时段" in msg
        assert "legal" in msg.lower() or "legal" in str(exc_info.value.context).lower()

    def test_prints_legal_notice(self):
        """无论通过与否，都应打印法律边界声明。"""
        dt = datetime(2026, 1, 4, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        stderr = io.StringIO()
        _legal_self_check(
            now=dt,
            allow_trading_hours=False,
            legal_ack=True,
            stdout=stderr,
        )
        output = stderr.getvalue()
        assert "法律边界" in output
        assert "Legal Boundary" in output

    def test_accepts_naive_datetime(self):
        """无时区的 datetime 应被自动附加时区。"""
        dt = datetime(2026, 1, 4, 10, 0, 0)  # no tzinfo
        # 不应抛出
        _legal_self_check(
            now=dt,
            allow_trading_hours=False,
            legal_ack=True,
            stdout=io.StringIO(),
        )

    def test_accepts_utc_datetime(self):
        """UTC 时区的 datetime 应被转换为 Asia/Shanghai。"""
        from datetime import timezone

        # 2026-01-05 02:00 UTC = 2026-01-05 10:00 CST (Monday, trading hours)
        dt = datetime(2026, 1, 5, 2, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(TdxError):
            _legal_self_check(
                now=dt,
                allow_trading_hours=False,
                legal_ack=True,
                stdout=io.StringIO(),
            )


# --------------------------------------------------------------------------- #
# _hex_dump
# --------------------------------------------------------------------------- #
class TestHexDump:
    def test_empty_data(self):
        result = _hex_dump(b"")
        assert result == ""

    def test_single_byte(self):
        result = _hex_dump(b"\x48")
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert "00000000" in lines[0]
        assert "48" in lines[0]
        assert "H" in lines[0]

    def test_16_bytes_one_row(self):
        data = bytes(range(16))  # 0x00 .. 0x0F
        result = _hex_dump(data)
        lines = result.strip().split("\n")
        assert len(lines) == 1
        line = lines[0]
        # Offset
        assert line.startswith("00000000")
        # Hex part contains all 16 bytes
        for i in range(16):
            assert f"{i:02x}" in line
        # ASCII part: 0x00-0x1f are non-printable, 0x20 is space, etc.
        assert "|" in line

    def test_32_bytes_two_rows(self):
        data = bytes(range(32))
        result = _hex_dump(data)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("00000000")
        assert lines[1].startswith("00000010")

    def test_offset_increment_by_16(self):
        data = bytes(64)
        result = _hex_dump(data)
        lines = result.strip().split("\n")
        assert len(lines) == 4
        for i, line in enumerate(lines):
            expected_offset = i * 16
            assert line.startswith(f"{expected_offset:08x}")

    def test_printable_chars_shown(self):
        data = b"Hello World!   "
        result = _hex_dump(data)
        assert "Hello World!" in result

    def test_non_printable_as_dot(self):
        """0x00-0x1f 显示为 '.'，0x20 显示为空格。"""
        # 4 non-printable + 1 printable (space)
        data = b"\x00\x01\x02\x1f\x20"
        result = _hex_dump(data)
        # ASCII column should be |.... | (4 dots + space)
        assert "|....|" in result or "|.... " in result

    def test_all_non_printable(self):
        """全部不可打印字符 → 全为 '.'。"""
        data = b"\x00\x01\x02\x03\x04"
        result = _hex_dump(data)
        # Should show 5 dots in ASCII column
        assert "....." in result or "|....." in result

    def test_ascii_column_delimiters(self):
        data = b"ABCDE"
        result = _hex_dump(data)
        # Each line should have | on both sides of the ASCII column
        for line in result.strip().split("\n"):
            assert line.count("|") == 2


# --------------------------------------------------------------------------- #
# _compress_payload
# --------------------------------------------------------------------------- #
class TestCompressPayload:
    def test_none_returns_none(self):
        result = _compress_payload(b"hello", "none")
        assert result is None

    def test_zlib_compresses(self):
        data = b"hello world " * 100
        result = _compress_payload(data, "zlib")
        assert result is not None
        compressed, codec = result
        assert codec == "zlib"
        # Should be smaller than original
        assert len(compressed) < len(data)
        # Round-trip
        assert zlib.decompress(compressed) == data

    def test_zlib_level_9(self):
        data = b"x" * 10000
        result = _compress_payload(data, "zlib")
        assert result is not None
        compressed, _ = result
        # Level 9 should give good compression
        assert len(compressed) < 100

    def test_zlib_short_data(self):
        """短数据压缩后可能反而变大——这是 zlib 的预期行为。"""
        data = b"\x00" * 4
        result = _compress_payload(data, "zlib")
        assert result is not None
        compressed, _ = result
        # 4 bytes may compress to more than 4 bytes due to zlib header
        # This is expected and fine
        assert zlib.decompress(compressed) == data

    def test_auto_falls_back_to_zlib_when_no_zstd(self):
        """auto 模式：zstandard 不可用时回退 zlib。"""
        data = b"hello world " * 100
        result = _compress_payload(data, "auto")
        assert result is not None
        compressed, codec = result
        # Should be zlib (zstandard is not installed in this environment)
        # If zstandard IS installed, codec would be "zstd"
        assert codec in ("zlib", "zstd")
        if codec == "zlib":
            assert zlib.decompress(compressed) == data
        else:
            # Verify round-trip with zstd
            import zstandard

            dctx = zstandard.ZstdDecompressor()
            assert dctx.decompress(compressed) == data

    def test_zstd_falls_back_to_zlib(self):
        """zstd 模式：zstandard 不可用时回落 zlib 并打印警告。"""
        data = b"hello world " * 100
        result = _compress_payload(data, "zstd")
        assert result is not None
        compressed, codec = result
        assert codec in ("zlib", "zstd")
        if codec == "zlib":
            assert zlib.decompress(compressed) == data

    def test_unknown_codec_returns_none(self):
        result = _compress_payload(b"data", "unknown_codec")
        assert result is None

    def test_empty_data(self):
        result = _compress_payload(b"", "zlib")
        assert result is not None
        compressed, _ = result
        assert zlib.decompress(compressed) == b""


# --------------------------------------------------------------------------- #
# write_sample metadata fields
# --------------------------------------------------------------------------- #
class TestMetadataTemplate:
    REQUIRED_FIELDS = [
        "schema",
        "source",
        "family",
        "command",
        "command_name",
        "tag",
        "note",
        "host",
        "local_host",
        "market",
        "code",
        "captured_at",
        "elapsed_ms",
        "request",
        "response",
        "record_size_hypothesis",
        "record_count_hypothesis",
        "compression",
        "parse_ctx",
        "tool",
        "legal",
    ]

    REQUIRED_REQUEST_FIELDS = ["method", "body_hex", "body_len"]
    REQUIRED_RESPONSE_FIELDS = [
        "zip_size",
        "unzip_size",
        "compressed",
        "payload_len",
        "sha256",
    ]
    REQUIRED_COMPRESSION_FIELDS = ["codec", "compressed_size"]
    REQUIRED_TOOL_FIELDS = ["name", "version"]
    REQUIRED_LEGAL_FIELDS = ["ack", "allow_trading_hours"]

    def _write_sample(self, base_dir: Path, options: CaptureOptions | None = None) -> dict:
        spec = _make_spec()
        frame = _make_frame()
        out_dir = base_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir,
            spec,
            frame,
            "10.0.0.1:7709",
            12.34,
            options=options or CaptureOptions(legal_ack=True),
        )
        meta_path = result_dir / "meta.json"
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def test_all_required_fields_present(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_FIELDS:
            assert field_name in meta, f"Missing required field: {field_name}"

    def test_request_fields(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_REQUEST_FIELDS:
            assert field_name in meta["request"], f"Missing request field: {field_name}"

    def test_response_fields(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_RESPONSE_FIELDS:
            assert field_name in meta["response"], f"Missing response field: {field_name}"

    def test_compression_fields(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_COMPRESSION_FIELDS:
            assert field_name in meta["compression"], f"Missing compression field: {field_name}"

    def test_tool_fields(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_TOOL_FIELDS:
            assert field_name in meta["tool"], f"Missing tool field: {field_name}"

    def test_legal_fields(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        for field_name in self.REQUIRED_LEGAL_FIELDS:
            assert field_name in meta["legal"], f"Missing legal field: {field_name}"

    def test_compression_none(self, tmp_dir):
        meta = self._write_sample(
            tmp_dir, options=CaptureOptions(compressor="none", legal_ack=True)
        )
        assert meta["compression"]["codec"] == "none"
        assert meta["compression"]["compressed_size"] is None

    def test_compression_zlib(self, tmp_dir):
        meta = self._write_sample(
            tmp_dir, options=CaptureOptions(compressor="zlib", legal_ack=True)
        )
        assert meta["compression"]["codec"] == "zlib"
        assert meta["compression"]["compressed_size"] is not None
        assert meta["compression"]["compressed_size"] > 0

    def test_legal_ack_reflected(self, tmp_dir):
        meta = self._write_sample(tmp_dir, options=CaptureOptions(legal_ack=True))
        assert meta["legal"]["ack"] is True
        assert meta["legal"]["allow_trading_hours"] is False

    def test_schema_version_2(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        assert meta["schema"] == 2

    def test_captured_at_utc_iso8601(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        # Should parse as a valid ISO 8601 datetime
        from datetime import datetime as dt

        parsed = dt.fromisoformat(meta["captured_at"])
        assert parsed.tzinfo is not None

    def test_sha256_matches_payload(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        _make_spec()
        frame = _make_frame()
        payload = frame.payload
        expected_sha = hashlib.sha256(payload).hexdigest()
        assert meta["response"]["sha256"] == expected_sha

    def test_command_hex_format(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        assert meta["command"].startswith("0x")
        assert meta["command"] == "0x530"

    def test_family_value(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        assert meta["family"] == "quotation"

    def test_host_field(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        assert meta["host"] == "10.0.0.1:7709"

    def test_response_sizes_consistent(self, tmp_dir):
        meta = self._write_sample(tmp_dir)
        resp = meta["response"]
        frame = _make_frame()
        assert resp["zip_size"] == frame.zip_size
        assert resp["unzip_size"] == frame.unzip_size
        assert resp["payload_len"] == len(frame.payload)


# --------------------------------------------------------------------------- #
# write_sample output files
# --------------------------------------------------------------------------- #
class TestWriteSampleOutputs:
    def test_payload_bin_written(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir, spec, frame, "host:7709", 5.0, options=CaptureOptions(legal_ack=True)
        )
        payload_bin = result_dir / "payload.bin"
        assert payload_bin.exists()
        assert payload_bin.read_bytes() == frame.payload

    def test_hex_txt_written_by_default(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir, spec, frame, "host:7709", 5.0, options=CaptureOptions(legal_ack=True)
        )
        hex_txt = result_dir / "payload.hex.txt"
        assert hex_txt.exists()
        content = hex_txt.read_text(encoding="utf-8")
        assert len(content) > 0
        # Should contain offset markers
        assert "00000000" in content

    def test_hex_txt_disabled(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir,
            spec,
            frame,
            "host:7709",
            5.0,
            options=CaptureOptions(legal_ack=True, dump_hex=False),
        )
        hex_txt = result_dir / "payload.hex.txt"
        assert not hex_txt.exists()

    def test_structure_md_written_by_default(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir, spec, frame, "host:7709", 5.0, options=CaptureOptions(legal_ack=True)
        )
        structure_md = result_dir / "structure.md"
        assert structure_md.exists()
        content = structure_md.read_text(encoding="utf-8")
        assert "Structure Analysis" in content
        assert "Frame Header" in content
        assert "Record Hypothesis" in content
        assert "0x530" in content or "0x0530" in content

    def test_structure_md_disabled(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir,
            spec,
            frame,
            "host:7709",
            5.0,
            options=CaptureOptions(legal_ack=True, dump_hex=False),
        )
        structure_md = result_dir / "structure.md"
        assert not structure_md.exists()

    def test_compressed_archive_written(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame(payload=b"x" * 200)
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir,
            spec,
            frame,
            "host:7709",
            5.0,
            options=CaptureOptions(legal_ack=True, compressor="zlib"),
        )
        archive = result_dir / "payload.zlib"
        assert archive.exists()
        compressed = archive.read_bytes()
        assert zlib.decompress(compressed) == frame.payload

    def test_no_compressed_archive_by_default(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir, spec, frame, "host:7709", 5.0, options=CaptureOptions(legal_ack=True)
        )
        assert not (result_dir / "payload.zlib").exists()
        assert not (result_dir / "payload.zstd").exists()

    def test_meta_yaml_written(self, tmp_dir):
        spec = _make_spec()
        frame = _make_frame()
        out_dir = tmp_dir / "golden"
        out_dir.mkdir(parents=True, exist_ok=True)
        result_dir = write_sample(
            out_dir, spec, frame, "host:7709", 5.0, options=CaptureOptions(legal_ack=True)
        )
        meta_yaml = result_dir / "meta.yaml"
        assert meta_yaml.exists()
        content = meta_yaml.read_text(encoding="utf-8")
        assert "schema:" in content
        assert "source:" in content


# --------------------------------------------------------------------------- #
# CaptureOptions dataclass
# --------------------------------------------------------------------------- #
class TestCaptureOptions:
    def test_defaults(self):
        opts = CaptureOptions()
        assert opts.legal_ack is False
        assert opts.allow_trading_hours is False
        assert opts.compressor == "none"
        assert opts.dump_hex is True

    def test_custom_values(self):
        opts = CaptureOptions(
            legal_ack=True,
            allow_trading_hours=True,
            compressor="zlib",
            dump_hex=False,
        )
        assert opts.legal_ack is True
        assert opts.allow_trading_hours is True
        assert opts.compressor == "zlib"
        assert opts.dump_hex is False
