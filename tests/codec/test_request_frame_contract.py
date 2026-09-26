from __future__ import annotations

import struct

import pytest

from tstdx.codec.framing import RequestFrame, build_request
from tstdx.errors import FramingError


def test_valid_request_frame_bytes_remain_canonical() -> None:
    body = b"\x01\x02\x03"
    frame = RequestFrame(method=0x052D, body=body, seq=7).encode()
    expected = struct.pack("<BIBHHH", 0x0C, 7, 0x01, 5, 5, 0x052D) + body

    assert frame == expected


def test_the_default_frame_header_sizes_come_from_the_struct_formats() -> None:
    """7709 标准布局的请求 12B / 响应 16B：这两个数只能由 ``*_header_fmt`` 现算出来。

    改前 ``FrameSpec`` 上还挂着 ``req_header_len=12`` / ``resp_header_len=16`` 两个没人读的
    手抄常数（第 25 轮 G36 删除）。它们与格式串之间没有任何约束，改了格式串不会有人发现
    ——所以这一格判据要的是"现算的值仍是这两个数"，不是"名单里还有这两个字段"。
    """
    from tstdx.codec.framing import DEFAULT_7709_SPEC

    assert (DEFAULT_7709_SPEC.req_header_size, DEFAULT_7709_SPEC.resp_header_size) == (12, 16)
    assert not any(
        name.endswith("_header_len") for name in DEFAULT_7709_SPEC.__dataclass_fields__
    ), "手抄长度字段又长回来了：同一事实的两份口径会各自过期"


@pytest.mark.parametrize("method", [-1, 0x10000, True, 1.5, "0x052d"])
def test_request_frame_rejects_non_uint16_method(method: object) -> None:
    with pytest.raises(FramingError, match="method"):
        RequestFrame(method=method, seq=1).encode()  # type: ignore[arg-type]


@pytest.mark.parametrize("seq", [-1, 0x1_0000_0000, True, 1.5, "1"])
def test_request_frame_rejects_non_uint32_sequence(seq: object) -> None:
    with pytest.raises(FramingError, match="seq"):
        RequestFrame(method=0x052D, seq=seq).encode()  # type: ignore[arg-type]


@pytest.mark.parametrize("body", [bytearray(b"x"), memoryview(b"x"), "x", None])
def test_request_frame_rejects_non_bytes_body(body: object) -> None:
    with pytest.raises(FramingError, match="body"):
        RequestFrame(method=0x052D, seq=1, body=body).encode()  # type: ignore[arg-type]


def test_build_request_rejects_invalid_identity_before_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compressed: list[bytes] = []

    def unexpected_compress(body: bytes) -> bytes:
        compressed.append(body)
        return body

    monkeypatch.setattr("tstdx.codec.framing.zlib_compress", unexpected_compress)

    with pytest.raises(FramingError, match="method"):
        build_request(-1, b"payload", compress=True)
    with pytest.raises(FramingError, match="body"):
        build_request(0x052D, bytearray(b"payload"), compress=True)  # type: ignore[arg-type]
    with pytest.raises(FramingError, match="compress"):
        build_request(0x052D, b"payload", compress=1)  # type: ignore[arg-type]
    with pytest.raises(FramingError, match="seq"):
        build_request(0x052D, b"payload", seq=-1)

    assert compressed == []


def test_build_request_accepts_uint_boundaries() -> None:
    frame, seq = build_request(0xFFFF, b"", seq=0xFFFFFFFF)

    assert seq == 0xFFFFFFFF
    assert frame[-2:] == b"\xff\xff"
