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
