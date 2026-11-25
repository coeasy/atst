# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P-6 回归：握手帧由 FrameSpec 生成，但线路字节与实测裸 hex 钉子完全相同。"""

from __future__ import annotations

from atst.codec.framing import DEFAULT_7709_SPEC, build_request
from atst.protocol.handshake import SETUP_FRAMES, setup_frames

#: 修复前的三帧裸 hex（帧 3 为 12 字节头 + 30 个零字节产品标识块）。
_PINNED = (
    "0c0218930001030003000d0001",
    "0c0218940001030003000d0002",
    "0c031899000120002000db0f" + "00" * 30,
)
_METHODS = (0x000D, 0x000D, 0x0FDB)
_BODIES = (b"\x01", b"\x02", b"\x00" * 30)
_SEQS = (0x00931802, 0x00941802, 0x00991803)


def test_setup_frames_emit_the_pinned_wire_bytes() -> None:
    """默认帧和模块级 SETUP_FRAMES 都必须逐字节等于旧 magic 常量。"""
    assert tuple(f.frame.hex() for f in SETUP_FRAMES) == _PINNED
    assert tuple(f.hex() for f in setup_frames()) == _PINNED


def test_setup_frames_are_built_through_the_frame_spec() -> None:
    """每帧都能用 build_request + 同一 spec 从固定 seq 重现。"""
    for method, body, seq, pinned in zip(_METHODS, _BODIES, _SEQS, _PINNED, strict=True):
        frame, used_seq = build_request(method, body, seq=seq, spec=DEFAULT_7709_SPEC)
        assert used_seq == seq
        assert frame.hex() == pinned


def test_frame3_header_is_sized_by_the_spec_and_only_body_is_overridden() -> None:
    """覆盖产品标识块只换 body，帧头长度取自 spec 且保持抓包原值。"""
    header_size = DEFAULT_7709_SPEC.req_header_size
    header = SETUP_FRAMES[2].frame[:header_size]
    assert header.hex() == _PINNED[2][: header_size * 2]

    blob = b"\x11" * 30
    frames = setup_frames(blob=blob)
    assert frames[2] == header + blob
    assert frames[:2] == tuple(f.frame for f in SETUP_FRAMES[:2])
