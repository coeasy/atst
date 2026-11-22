# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Client.local_finance：本地 gpcw 财务读取（离线、语义字段、沪深北边界）。"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atst import Client
from atst.errors import ValidationError

pytestmark = pytest.mark.unit


def _seed_gpcw(root: Path) -> None:
    """造一个 28 字段 gpcw 文件：字段 20=净利润、24=EPS，见 GPCW_FIELD_NAMES。"""
    sh = root / "sh"
    sh.mkdir(parents=True, exist_ok=True)
    values = [0.0] * 28
    values[20] = 1234.5  # net_profit
    values[24] = 2.5  # eps
    (sh / "gpcw.dat").write_bytes(struct.pack("<28f", *values))


def test_local_finance_reads_semantic_fields(tmp_path: Path) -> None:
    _seed_gpcw(tmp_path)
    rows = Client().local_finance("sh600519", root=str(tmp_path))
    assert len(rows) == 1
    assert rows[0]["code"] == "sh600519"
    assert rows[0]["net_profit"] == pytest.approx(1234.5)
    assert rows[0]["eps"] == pytest.approx(2.5)


def test_local_finance_requires_root(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="root|vipdoc_root"):
        Client().local_finance("sh600519")


def test_local_finance_rejects_non_a_share(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="沪深北"):
        Client().local_finance("hk00700", root=str(tmp_path))


def test_local_finance_missing_file_returns_empty(tmp_path: Path) -> None:
    assert Client().local_finance("sh600519", root=str(tmp_path)) == []
