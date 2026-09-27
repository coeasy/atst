"""F2 批次 codec 收口测试：count_guard 助手 / zlib strict 默认 / iter_frames 帧长守卫 /
ResponseFrame.ok 使用 spec.magic。

对应任务：T2/T3 慢解析防御的 codec 侧原语、framing.iter_frames 对齐
transport/base.py read_frame 的 max_frame_bytes 语义、ResponseFrame.ok 改用
解析所用 spec 的 magic。
"""

from __future__ import annotations

import struct
import zlib

import pytest

from atst.codec.framing import (
    DEFAULT_7709_SPEC,
    MAX_FRAME_BYTES,
    FrameSpec,
    ResponseFrame,
    iter_frames,
    parse_response_header,
)
from atst.codec.primitive import BinaryReader, count_guard, zlib_compress, zlib_decompress
from atst.errors import FramingError


def _resp_header(
    *,
    magic: int = 0x0074CBB1,
    seq: int = 1,
    method: int = 0x052D,
    zip_size: int = 10,
    unzip_size: int = 10,
) -> bytes:
    return struct.pack("<IBIBHHH", magic, 0x0C, seq, 0, method, zip_size, unzip_size)


@pytest.mark.unit
class TestCountGuard:
    """count_guard：T2/T3 慢解析防御的上限收敛。"""

    def test_module_function_clamps_to_capacity(self):
        # 45 字节 / 每条 10B → 最多 4 条，声明 65535 被钳制
        assert count_guard(65535, 10, 45) == 4

    def test_module_function_identity_when_fits(self):
        assert count_guard(4, 10, 45) == 4
        assert count_guard(4, 10, 40) == 4

    def test_module_function_zero_and_degenerate(self):
        assert count_guard(0, 10, 100) == 0
        assert count_guard(-3, 10, 100) == 0  # 负数无意义，返回 0
        assert count_guard(5, 0, 100) == 5  # min_rec<=0 不干预
        assert count_guard(5, 10, -1) == 0  # 负 remaining → 无法容纳任何记录

    def test_reader_method_uses_remaining(self):
        reader = BinaryReader(b"\xff" * 45)
        reader.uint16()  # 模拟 count 已读后的场景：剩余 43
        assert reader.count_guard(65535, 10) == 4  # 43 // 10 = 4

    def test_reader_method_no_clamp_when_fits(self):
        reader = BinaryReader(b"\x00" * 100)
        assert reader.count_guard(10, 10) == 10


@pytest.mark.unit
class TestZlibStrictDefault:
    """zlib_decompress 默认 strict=True（F2 收紧；全仓唯一调用点本就传 strict=True）。"""

    def test_garbage_raises_by_default(self):
        with pytest.raises(zlib.error):
            zlib_decompress(b"this is definitely not a deflate stream \x01\x02\x03")

    def test_lenient_mode_still_available(self):
        body = b"raw bytes that fail to decompress"
        assert zlib_decompress(body, strict=False) == body

    def test_valid_deflate_roundtrip(self):
        payload = b"\x02\x00" + b"\x01\x02\x03\x04" * 8
        packed = zlib_compress(payload)
        assert zlib_decompress(packed) == payload

    def test_empty_body_returns_empty(self):
        assert zlib_decompress(b"") == b""


@pytest.mark.unit
class TestIterFramesMaxFrameBytes:
    """iter_frames 帧长守卫：对齐 transport/base.py read_frame 的 max_frame_bytes 语义。"""

    def test_valid_stream_still_yields(self):
        body = b"\x02\x00" + b"\x01\x02\x03\x04\x05\x06\x07\x08"
        stream = _resp_header(zip_size=len(body), unzip_size=len(body)) + body
        frames = list(iter_frames(stream))
        assert len(frames) == 1
        assert frames[0].method == 0x052D
        assert frames[0].payload == body
        assert frames[0].ok

    def test_oversized_zip_size_raises_framing_error(self):
        # zip_size=40000 > MAX_FRAME_BYTES(32768)：错位流上的垃圾长度字段必须立即失败
        stream = _resp_header(zip_size=MAX_FRAME_BYTES + 1, unzip_size=MAX_FRAME_BYTES + 1)
        with pytest.raises(FramingError, match="响应帧过大"):
            list(iter_frames(stream))

    def test_custom_spec_limit_respected(self):
        small_spec = FrameSpec(max_frame_bytes=32)
        body = b"\x00" * 64  # zip_size=64 > 32
        stream = _resp_header(zip_size=64, unzip_size=64) + body
        with pytest.raises(FramingError, match="响应帧过大"):
            list(iter_frames(stream, small_spec))


@pytest.mark.unit
class TestResponseFrameOkUsesSpecMagic:
    """ResponseFrame.ok 按「解析本帧所用 spec」的 magic 自检，而非模块级常量。"""

    def test_custom_magic_spec_ok_true(self):
        spec = FrameSpec(magic=0x00F00DBA)
        frame = parse_response_header(
            _resp_header(magic=0x00F00DBA, zip_size=4, unzip_size=4), spec
        )
        frame.payload = b"\x00" * 4
        assert frame.ok is True

    def test_custom_magic_frame_fails_default_spec_check(self):
        # 同一帧用默认 spec 校验：magic 不符 → ok=False
        frame = parse_response_header(
            _resp_header(magic=0x00F00DBA, zip_size=4, unzip_size=4), DEFAULT_7709_SPEC
        )
        frame.payload = b"\x00" * 4
        assert frame.ok is False

    def test_direct_construction_keeps_standard_magic(self):
        # 直接构造（历史调用形态）不传 spec_magic → 缺省标准 magic，行为不变
        frame = ResponseFrame(
            magic=0x0074CBB1,
            zip_flag=0,
            seq=0,
            method=0x052D,
            zip_size=2,
            unzip_size=2,
            payload=b"\x00\x00",
        )
        assert frame.ok is True
