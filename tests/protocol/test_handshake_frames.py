# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""握手三帧的线路形状（F-59）。

此前 ``atst/protocol/handshake.py`` 没有任何测试，而它帧 3 的默认内容恰好是
F-37 的成因：主站对"重放自采集产品标识块"的会话只回 2 字节空 K 线，对全零块
回真实数据（``b"A\"*30"`` / ``b"\\xff\"*30"`` 也回真实数据，所以关键不是零本身，
而是别把抓包样本当默认值重放）。默认值因此是承重决策，必须钉住线路形状，
而不是留在文档里——服务端反应不在离线射程内，模块 docstring 已写明这点。
"""

from __future__ import annotations

import pytest

from atst.protocol.commands import Family
from atst.protocol.handshake import SETUP_FRAMES, setup_frames

FRAME3_LEN = 30


def test_frame3_default_product_block_is_thirty_zero_bytes() -> None:
    """帧 3 默认必须是 30 个零字节——换回自采集样本即回归。"""
    frame3 = SETUP_FRAMES[-1]
    assert frame3.method == 0x0FDB
    assert frame3.body == b"\x00" * FRAME3_LEN
    assert frame3.frame == _header3() + frame3.body


def test_frame3_pkg_len_still_advertises_the_body() -> None:
    """内容换成零字节后，帧头的 pkg_len 仍等于 ``len(body) + 2``。"""
    frame = setup_frames()[2]
    pkg_len = int.from_bytes(frame[6:8], "little")
    assert pkg_len == FRAME3_LEN + 2
    assert int.from_bytes(frame[8:10], "little") == pkg_len
    assert int.from_bytes(frame[10:12], "little") == 0x0FDB


def test_frames_one_and_two_are_unchanged_step_echoes() -> None:
    assert [f.method for f in SETUP_FRAMES[:2]] == [0x000D, 0x000D]
    assert [f.body for f in SETUP_FRAMES[:2]] == [b"\x01", b"\x02"]


@pytest.mark.parametrize("family", [Family.STANDARD, Family.MAC])
def test_override_replaces_only_frame3_body(family: str) -> None:
    blob = b"\x11" * FRAME3_LEN
    frames = setup_frames(family, blob=blob)
    assert len(frames) == 3
    assert frames[2] == _header3() + blob
    assert frames[:2] == setup_frames(family)[:2]


def test_override_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="30"):
        setup_frames(blob=b"\x00" * 29)


@pytest.mark.parametrize("family", [Family.EXTENDED, Family.F10, Family.GOODS])
def test_families_without_handshake_send_nothing(family: str) -> None:
    assert setup_frames(family) == ()


def _header3() -> bytes:
    """帧 3 的 12 字节帧头（从默认帧反推，避免把常量抄第二遍）。"""
    return SETUP_FRAMES[2].frame[:12]
