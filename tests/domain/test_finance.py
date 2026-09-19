# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F1：财务 / 除权除息语义层测试。

覆盖 :mod:`tstdx.domain.finance`：
* ``map_finance_values``：数值数组 → 带字段名字典（含未知索引 f{n} 兜底、drop_zero）；
* ``to_capital_changes``：0x000F 解析行 → :class:`CapitalChange`（缺字段容忍）；
* 客户端/解析器复用：``client._row_to_capital`` 委托同一转换器。
"""

from __future__ import annotations

import pytest

from tstdx.client import _row_to_capital
from tstdx.domain.finance import (
    FINANCE_INFO_FIELDS,
    GPCW_FIELD_NAMES,
    map_finance_values,
    to_capital_changes,
)
from tstdx.domain.models import CapitalChange

pytestmark = pytest.mark.unit


class TestMapFinanceValues:
    def test_known_fields_named(self) -> None:
        values = [100.0, 50.0, 0.0, 2.5]
        out = map_finance_values(values, GPCW_FIELD_NAMES)
        assert out["total_shares"] == 100.0
        assert out["state_shares"] == 50.0
        assert out["promoter_legal_shares"] == 0.0  # 零值默认保留
        assert out["legal_shares"] == 2.5

    def test_unknown_index_fallback(self) -> None:
        # 索引 27（gpcw 扩展字段）未被 GPCW_FIELD_NAMES 覆盖 → f27
        values = [0.0] * 28
        values[27] = 1.25
        out = map_finance_values(values, GPCW_FIELD_NAMES)
        assert out["f27"] == 1.25

    def test_drop_zero(self) -> None:
        out = map_finance_values([1.0, 0.0, 2.0], GPCW_FIELD_NAMES, drop_zero=True)
        assert "state_shares" not in out
        assert out["total_shares"] == 1.0

    def test_finance_info_fields_distinct_names(self) -> None:
        """0x0010 与 gpcw 字段序不同源（各自独立映射）。"""
        assert FINANCE_INFO_FIELDS[0] == "float_shares"
        assert GPCW_FIELD_NAMES[0] == "total_shares"

    def test_values_rounded_six(self) -> None:
        out = map_finance_values([1.0 / 3.0], GPCW_FIELD_NAMES)
        assert out["total_shares"] == pytest.approx(0.333333, abs=1e-6)


class TestToCapitalChanges:
    def test_full_row(self) -> None:
        rows = [
            {
                "code": "600519",
                "market": 1,
                "category": 1,
                "category_name": "除权除息",
                "date": "2024-06-03",
                "dividend": 30.0,
                "rights_price": 0.0,
                "bonus_ratio": 0.0,
                "rights_ratio": 0.0,
            }
        ]
        ev = to_capital_changes(rows)[0]
        assert isinstance(ev, CapitalChange)
        assert ev.code == "600519"
        assert ev.dividend == 30.0
        assert ev.date == "2024-06-03"

    def test_missing_fields_tolerated(self) -> None:
        """record_size < 29 时无 dividend/rights 字段 → 缺省 0（不炸）。"""
        ev = to_capital_changes([{"code": "000001", "date": "2023-01-01"}])[0]
        assert ev.dividend == 0.0
        assert ev.rights_price == 0.0
        assert ev.bonus_ratio == 0.0
        assert ev.rights_ratio == 0.0

    def test_client_row_to_capital_delegates(self) -> None:
        """client._row_to_capital 与共享转换器输出一致（单一事实源）。"""
        row = {
            "code": "600000",
            "market": 1,
            "category": 1,
            "date": "2024-06-03",
            "dividend": 10.0,
        }
        shared = to_capital_changes([row])[0]
        via_client = _row_to_capital(row)
        assert via_client.to_dict() == shared.to_dict()

    def test_to_dict_roundtrip(self) -> None:
        ev = to_capital_changes([{"code": "600519", "date": "2024-06-03", "dividend": 30.0}])[0]
        d = ev.to_dict()
        assert d["dividend"] == 30.0
        assert d["category"] == 0
