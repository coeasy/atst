# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""F1：``FinanceReader`` gpcw 语义化读取测试。

覆盖 :meth:`atst.reader.formats.FinanceReader.read_indicators`：
合成 gpcw 文件（float32 扁平数组，28/30 字段/记录）→ 带字段名财务指标。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from atst.reader import FinanceReader

pytestmark = pytest.mark.unit

#: gpcw.txt 约定字段序中的关键索引
_TOTAL_SHARES = 0
_STATE_SHARES = 1
_LEGAL_SHARES = 3
_NET_ASSETS_PER_SHARE = 22
_CAPITAL_RESERVE_PER_SHARE = 23
_EPS = 24
_RETAINED_PER_SHARE = 25
_OCF_PER_SHARE = 26


def _rec28(
    *,
    total: float,
    state: float = 0.0,
    legal: float = 0.0,
    nps: float = 0.0,
    cps: float = 0.0,
    eps: float = 0.0,
    rep: float = 0.0,
    ocps: float = 0.0,
) -> list[float]:
    """构造 28 字段 gpcw 记录（其余字段置 0）。"""
    v = [0.0] * 28
    v[_TOTAL_SHARES] = total
    v[_STATE_SHARES] = state
    v[_LEGAL_SHARES] = legal
    v[_NET_ASSETS_PER_SHARE] = nps
    v[_CAPITAL_RESERVE_PER_SHARE] = cps
    v[_EPS] = eps
    v[_RETAINED_PER_SHARE] = rep
    v[_OCF_PER_SHARE] = ocps
    return v


def _build_gpcw(path: Path, records: list[list[float]]) -> None:
    n_fields = len(records[0])
    raw = b"".join(struct.pack(f"<{n_fields}f", *r) for r in records)
    path.write_bytes(raw)


def test_read_indicators_named_fields(tmp_path: Path) -> None:
    p = tmp_path / "gpcw.dat"
    _build_gpcw(
        p,
        [
            _rec28(
                total=100.0, state=50.0, legal=30.0, nps=1.5, cps=0.8, eps=1.2, rep=0.5, ocps=0.1
            ),
            _rec28(total=200.0, legal=80.0, nps=2.0, cps=1.1, eps=1.6, rep=0.8, ocps=0.2),
        ],
    )
    out = FinanceReader().read_indicators(p)
    assert len(out) == 2
    row = out[0]
    assert row["total_shares"] == 100.0
    assert row["state_shares"] == 50.0
    assert row["legal_shares"] == 30.0
    assert row["net_assets_per_share"] == 1.5
    assert row["capital_reserve_per_share"] == 0.8
    assert row["eps"] == 1.2
    assert row["retained_earnings_per_share"] == 0.5
    assert row["operating_cashflow_per_share"] == 0.1
    assert row["date"] == ""  # read_indicators 记录无日期

    assert out[1]["total_shares"] == 200.0
    assert out[1]["eps"] == 1.6


def test_read_indicators_unknown_index_fallback(tmp_path: Path) -> None:
    """超过 FIELD_NAMES 覆盖范围的扩展字段按 f{n} 保留。"""
    p = tmp_path / "gpcw30.dat"
    n_fields = 30
    values = _rec28(total=1.0) + [0.0, 0.0]
    values[27] = 7.77  # gpcw 30 字段版本第 28 个字段（未映射）
    values[29] = 8.88
    assert len(values) == n_fields
    _build_gpcw(p, [values])
    out = FinanceReader().read_indicators(p)
    assert out[0]["total_shares"] == 1.0
    assert out[0]["f27"] == 7.77
    assert out[0]["f29"] == 8.88


def test_read_preserves_raw_values(tmp_path: Path) -> None:
    """旧 ``read()`` 兼容：仍返回 ``{"values": [...]}``，不破坏既有契约。"""
    p = tmp_path / "gpcw.dat"
    values = _rec28(total=1.0, eps=2.0)
    _build_gpcw(p, [values])
    out = FinanceReader().read(p)
    assert out == [{"values": values}]


def test_read_indicators_tuple_output(tmp_path: Path) -> None:
    p = tmp_path / "gpcw.dat"
    _build_gpcw(p, [_rec28(total=1.0)])
    out = FinanceReader().read_indicators(p, output="tuple")
    assert len(out) == 1
    assert out[0][0] == ""  # 首元素 code=""
    assert 1.0 in out[0]
