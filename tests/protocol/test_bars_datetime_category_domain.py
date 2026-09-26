"""7709 K 线 datetime 的周期域必须收口：两族之外的 category 拒绝猜测（F-78）。

`_read_datetime` 此前只判分钟族，其余一律按 uint32 当 YYYYMMDD——于是任何一个不在
已证逆向布局里的 category 会读掉 4 个字节并把整行后续字段全部错位，调用方拿到的
是"看起来成功"的脏数据。本文件钉住两件事：声明过的每个周期都有已证布局，且越域
当场抛 :class:`~tstdx.errors.ProtocolError`。
"""

from __future__ import annotations

import struct

import pytest

from tstdx.codec.primitive import BinaryReader
from tstdx.errors import ProtocolError
from tstdx.protocol.parsers._std7709_bars import SecurityBarsParser
from tstdx.protocol.parsers._std7709_common import (
    DAYLIKE_CATEGORIES,
    MINUTELIKE_CATEGORIES,
    KlineCategory,
)


def test_declared_periods_all_have_a_proven_datetime_layout() -> None:
    """两个集合的并恰好覆盖 `KlineCategory.NAMES`：没有周期靠回退分支蒙。"""

    assert frozenset() == MINUTELIKE_CATEGORIES & DAYLIKE_CATEGORIES
    assert set(KlineCategory.NAMES) == set(MINUTELIKE_CATEGORIES | DAYLIKE_CATEGORIES)


def test_category_outside_both_families_fails_closed() -> None:
    """越域 category 必须报错，而不是按 uint32 猜一个日期。"""

    reader = BinaryReader(b"\x00\x00\x2a\x07")
    with pytest.raises(ProtocolError) as caught:
        SecurityBarsParser._read_datetime(reader, 12)
    assert caught.value.code == "E3000"
    assert "category=12" in str(caught.value)
    assert reader.pos == 0, "拒绝猜测时不能已经吃掉字节"


@pytest.mark.parametrize("category", sorted(DAYLIKE_CATEGORIES))
def test_daylike_reads_uint32_yyyymmdd(category: int) -> None:
    reader = BinaryReader(struct.pack("<I", 20260925))
    assert SecurityBarsParser._read_datetime(reader, category) == (2026, 9, 25, 15, 0)


@pytest.mark.parametrize("category", sorted(MINUTELIKE_CATEGORIES))
def test_minutelike_reads_two_uint16(category: int) -> None:
    raw_date = (2026 - 2004) * 2048 + 9 * 100 + 25
    reader = BinaryReader(struct.pack("<HH", raw_date, 14 * 60 + 30))
    assert SecurityBarsParser._read_datetime(reader, category) == (2026, 9, 25, 14, 30)
