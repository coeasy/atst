# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""解码原语的离线确定性契约：读写器对称性、变长编码、字符集探测。

这些函数是全库解析的地基，此前仅被上层解析器间接覆盖，边界分支
（越界、未知编码、NUL 语义、count 钳制）无用例。本文件把它们钉死。
"""

from __future__ import annotations

import struct

import pytest

from atst.codec.primitive import (
    BinaryReader,
    BinaryWriter,
    count_guard,
    decode_gbk,
    decode_leb128,
    decode_price,
    decode_varint,
    decode_volume,
    detect_encoding,
    encode_leb128,
    encode_varint,
    get_datetime_from_lc,
    minutes_to_hhmm,
)
from atst.errors import ParseError

pytestmark = pytest.mark.unit


class TestBinaryReaderScalars:
    def test_reads_every_fixed_scalar(self) -> None:
        buf = struct.pack(
            "<BBhHiiQqfd",
            255,
            1,
            -2,
            3,
            -4,
            5,
            6,
            -7,
            1.5,
            2.5,
        )
        reader = BinaryReader(buf)
        assert reader.uint8() == 255
        assert reader.int8() == 1
        assert reader.int16() == -2
        assert reader.uint16() == 3
        assert reader.int32() == -4
        assert reader.uint32() == 5
        assert reader.uint64() == 6
        assert reader.int64() == -7
        assert reader.float32() == pytest.approx(1.5)
        assert reader.float64() == pytest.approx(2.5)
        assert reader.remaining == 0

    def test_big_endian_reader_flips_byte_order(self) -> None:
        assert BinaryReader(struct.pack(">H", 1), endian=">").uint16() == 1

    def test_skip_and_seek_move_the_cursor(self) -> None:
        reader = BinaryReader(b"\x01\x02\x03\x04")
        reader.skip(2)
        assert reader.uint8() == 3
        reader.seek(0)
        assert reader.bytes(2) == b"\x01\x02"
        assert reader.rest() == b"\x03\x04"

    @pytest.mark.parametrize("action", ["over_read", "over_skip", "bad_seek"])
    def test_boundary_violations_raise_parse_error(self, action: str) -> None:
        reader = BinaryReader(b"\x01\x02")
        with pytest.raises(ParseError):
            if action == "over_read":
                reader.uint32()
            elif action == "over_skip":
                reader.skip(3)
            else:
                reader.seek(99)

    def test_negative_seek_raises(self) -> None:
        with pytest.raises(ParseError):
            BinaryReader(b"\x01").seek(-1)


class TestBinaryReaderStrings:
    def test_string_strips_nul_padding(self) -> None:
        assert BinaryReader("中国".encode("gbk") + b"\x00" * 4).string(8) == "中国"

    def test_cstring_consumes_the_terminator(self) -> None:
        reader = BinaryReader(b"abc\x00\x07")
        assert reader.cstring() == "abc"
        assert reader.uint8() == 7

    def test_cstring_without_terminator_raises(self) -> None:
        with pytest.raises(ParseError, match="NUL"):
            BinaryReader(b"abc").cstring()


class TestBinaryWriter:
    def test_writer_is_the_exact_inverse_of_the_reader(self) -> None:
        payload = (
            BinaryWriter()
            .uint8(0x1FF)
            .uint16(0x1FFFF)
            .uint32(0x1FFFFFFFF)
            .int32(-5)
            .float32(1.5)
            .string("ab", 4)
            .raw(b"\xff")
            .to_bytes()
        )
        assert (
            payload == struct.pack("<BHIif", 0xFF, 0xFFFF, 0xFFFFFFFF, -5, 1.5) + b"ab\x00\x00\xff"
        )
        reader = BinaryReader(payload)
        assert (reader.uint8(), reader.uint16(), reader.uint32()) == (0xFF, 0xFFFF, 0xFFFFFFFF)
        assert reader.int32() == -5
        assert reader.float32() == pytest.approx(1.5)
        assert reader.string(4) == "ab"
        assert reader.uint8() == 0xFF

    def test_string_truncates_to_the_declared_width(self) -> None:
        assert BinaryWriter().string("abcdef", 3).to_bytes() == b"abc"


class TestVarint:
    @pytest.mark.parametrize("value", [0, 1, 63, 64, 4095, 4096, 5787, 2**35])
    def test_round_trip(self, value: int) -> None:
        encoded = encode_varint(value)
        decoded, next_pos = decode_varint(encoded)
        assert (decoded, next_pos) == (value, len(encoded))

    def test_continuation_flag_layout(self) -> None:
        assert encode_varint(64) == b"\x40\x01"

    def test_negative_value_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="非负"):
            encode_varint(-1)

    def test_truncated_stream_raises(self) -> None:
        with pytest.raises(ParseError, match="越界"):
            decode_varint(b"\x40")

    def test_overlong_stream_raises(self) -> None:
        with pytest.raises(ParseError, match="过长"):
            decode_varint(b"\xff" * 14)


class TestLeb128AndPrice:
    @pytest.mark.parametrize("value", [0, 1, -1, 32, -32, 8192, -8192])
    def test_signed_round_trip(self, value: int) -> None:
        assert decode_leb128(encode_leb128(value)) == (value, len(encode_leb128(value)))

    def test_decode_price_applies_the_profile_scale(self) -> None:
        assert decode_price(encode_leb128(25), scale=1000) == (0.025, 1)

    def test_zero_scale_falls_back_to_the_raw_unit(self) -> None:
        assert decode_price(encode_leb128(25), scale=0)[0] == 25.0


class TestVolumeAndAmountEncodings:
    def test_uint32(self) -> None:
        assert decode_volume(struct.pack("<I", 700), encoding="uint32") == (700.0, 4)

    def test_float32(self) -> None:
        assert decode_volume(struct.pack("<f", 1.25), encoding="float32") == (1.25, 4)

    def test_varint(self) -> None:
        encoded = encode_varint(4096)
        assert decode_volume(encoded, encoding="varint") == (4096.0, len(encoded))

    @pytest.mark.parametrize("encoding", ["uint32", "float32"])
    def test_short_buffer_raises(self, encoding: str) -> None:
        with pytest.raises(ParseError, match="成交量越界"):
            decode_volume(b"\x01\x02", encoding=encoding)

    def test_unknown_encoding_raises(self) -> None:
        with pytest.raises(ParseError, match="未知成交量编码"):
            decode_volume(b"\x00" * 4, encoding="int48")


class TestCountGuard:
    def test_clamps_to_the_bytes_actually_available(self) -> None:
        assert count_guard(0xFFFF, 32, 100) == 3

    @pytest.mark.parametrize(
        ("declared", "record_bytes", "remaining"),
        [(0, 32, 100), (-1, 32, 100), (4, 0, 100)],
    )
    def test_meaningless_guards_are_left_alone(
        self, declared: int, record_bytes: int, remaining: int
    ) -> None:
        assert count_guard(declared, record_bytes, remaining) == max(declared, 0)

    def test_negative_remaining_clamps_to_zero(self) -> None:
        assert count_guard(4, 32, -8) == 0


class TestLocalTimeEncodings:
    def test_lc16_date(self) -> None:
        assert get_datetime_from_lc(2048 * 22 + 831) == (2026, 8, 31)

    def test_minutes_since_midnight(self) -> None:
        assert minutes_to_hhmm(0) == (0, 0)
        assert minutes_to_hhmm(570) == (9, 30)
        assert minutes_to_hhmm(1439) == (23, 59)


class TestCharset:
    def test_detect_prefers_gbk_for_chinese_payload(self) -> None:
        assert detect_encoding("平安银行".encode("gbk")) == "gbk"

    def test_detect_falls_back_to_the_widest_superset(self) -> None:
        assert detect_encoding(b"\xff\xff\xff\xff") == "gb18030"

    def test_gbk_decoder_survives_an_unknown_encoding_name(self) -> None:
        assert decode_gbk("平安银行".encode("gbk"), "no-such-codec") == "平安银行"

    def test_nul_strip_modes_differ_on_embedded_nul(self) -> None:
        raw = b"body\x00tail\x00\x00"
        assert decode_gbk(raw) == "body"
        assert decode_gbk(raw, strip_nul="tail") == "body\x00tail"
