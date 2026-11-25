# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""L2 启发式不得把「全零 / 单字段 + 大片零」载荷抬成表格（2026-11-25 真机语料回归）。

对 ``PROTOCOL_SPEC/_sniffer`` 的 12 份自采集样本实测：06b9 / 000f / 0200 共 9 份
载荷此前全部拿到 0.85 分并落 L2，但它们没有可验证的表结构，属于典型假阳性。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atst.codec.framing import ResponseFrame
from atst.protocol.commands import Family
from atst.protocol.generic import TIER_L2, TIER_L3, infer_record_layout, parse_generic

_SNIFFER = Path(__file__).resolve().parents[2] / "PROTOCOL_SPEC" / "_sniffer"


def _frame(payload: bytes) -> ResponseFrame:
    return ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=0,
        seq=1,
        method=0xCAFE,
        zip_size=len(payload),
        unzip_size=len(payload),
        payload=payload,
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"\x00" * 4,  # 06b9：一个零 u32 伪装成一行四字节记录
        struct.pack("<I", 300) + b"\x00" * 28,  # 000f/0200：一个非零值 + 七段零
    ],
)
def test_synthetic_degenerate_payload_is_l3(payload: bytes) -> None:
    cand = infer_record_layout(payload)
    assert cand.score < 0.5
    assert any("近乎全零" in note for note in cand.notes)
    assert parse_generic(_frame(payload), family=Family.STANDARD).tier == TIER_L3


@pytest.mark.parametrize("rel_dir", ["f10/06b9", "quotation/000f", "quotation/0200"])
def test_archived_degenerate_samples_are_l3(rel_dir: str) -> None:
    samples = sorted((_SNIFFER / rel_dir).glob("*.bin"))
    assert samples, f"缺少真机样本目录: {rel_dir}"
    for path in samples:
        payload = path.read_bytes()
        cand = infer_record_layout(payload)
        assert cand.score < 0.5, f"{path} 仍被抬成 L2: {cand.to_dict()}"
        assert parse_generic(_frame(payload), family=Family.STANDARD).tier == TIER_L3


def test_varied_single_record_is_not_penalized() -> None:
    """0530 快照虽只有一条记录，但字段类型多样，不能被退化罚误伤。"""
    payload = (_SNIFFER / "std7709" / "0530" / "20261013T210313167.bin").read_bytes()
    cand = infer_record_layout(payload)
    assert cand.score >= 0.5
    assert cand.record_size == 29
    assert not any("近乎全零" in note for note in cand.notes)
    assert parse_generic(_frame(payload), family=Family.STANDARD).tier == TIER_L2
