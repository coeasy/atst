# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""F1：财务 / 除权除息语义层测试。

覆盖 :mod:`atst.domain.finance`：
* ``map_finance_values``：数值数组 → 带字段名字典（含未知索引 f{n} 兜底、drop_zero）；
* ``to_capital_changes``：0x000F 解析行 → :class:`CapitalChange`（缺字段容忍）；
* 客户端/解析器复用：``client._row_to_capital`` 委托同一转换器。
"""

from __future__ import annotations

import pytest

from atst.client import _row_to_capital
from atst.domain.finance import (
    FINANCE_INFO_FIELDS,
    GPCW_FIELD_NAMES,
    map_finance_values,
    to_capital_changes,
)
from atst.domain.models import CapitalChange

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


class TestDividendRightsMerge:
    """分红送转 + 配股按除权日合成为**一天一条**（B3 修复）。

    为什么必须合并：除权参考价 ``P_ref = (P - D + Pr·R)/(1+S+R)`` 是一个整体公式。
    同一天既有送转派息又有配股时（``10送2配3派1`` 这类组合方案很常见），拆成两条
    事件各自算一次再连乘，分母里的交叉项 ``S·R`` 会丢，因子系统性偏大。
    """

    def test_same_day_merges_into_one_event(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        dividends = [
            {"EX_DIVIDEND_DATE": "2010-07-01", "BONUS_RATIO": 2.0, "PRETAX_BONUS_RMB": 1.0}
        ]
        rights = [{"EX_DIVIDEND_DATE": "2010-07-01", "PLACING_RATIO": 3.0, "ISSUE_PRICE": 8.85}]
        events = capital_changes_from_dividends_and_rights(dividends, rights, code="600036")
        assert len(events) == 1
        ev = events[0]
        assert ev.date == "2010-07-01"
        assert ev.bonus_ratio == pytest.approx(2.0)
        assert ev.dividend == pytest.approx(1.0)
        assert ev.rights_ratio == pytest.approx(3.0)
        assert ev.rights_price == pytest.approx(8.85)

    def test_rights_only_day_still_produces_event(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        rights = [{"EX_DIVIDEND_DATE": "2010-11-05", "PLACING_RATIO": 1.3, "ISSUE_PRICE": 8.85}]
        events = capital_changes_from_dividends_and_rights([], rights, code="600036")
        assert len(events) == 1
        assert events[0].rights_ratio == pytest.approx(1.3)
        assert events[0].dividend == pytest.approx(0.0)
        assert events[0].bonus_ratio == pytest.approx(0.0)

    def test_rows_without_ex_date_are_dropped(self) -> None:
        #: 拿报告期当事件日会把因子提前整整一个季度，所以没有除权日的行一律丢。
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        dividends = [{"REPORT_DATE": "2010-06-30", "BONUS_RATIO": 5.0}]
        rights = [{"ISSUE_DATE": "2010-06-30", "PLACING_RATIO": 3.0}]
        assert capital_changes_from_dividends_and_rights(dividends, rights) == []

    def test_two_days_sorted_ascending(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        dividends = [
            {"EX_DIVIDEND_DATE": "2020-07-01", "PRETAX_BONUS_RMB": 1.0},
            {"EX_DIVIDEND_DATE": "2015-07-01", "PRETAX_BONUS_RMB": 2.0},
        ]
        events = capital_changes_from_dividends_and_rights(dividends)
        assert [e.date for e in events] == ["2015-07-01", "2020-07-01"]

    def test_zero_rights_price_does_not_overwrite(self) -> None:
        #: 配股价缺列/为 0 时不许把已有值抹成 0——0 元配股会让除权价公式失真。
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        rights = [
            {"EX_DIVIDEND_DATE": "2010-07-01", "PLACING_RATIO": 3.0, "ISSUE_PRICE": 8.85},
            {"EX_DIVIDEND_DATE": "2010-07-01", "PLACING_RATIO": 0.0, "ISSUE_PRICE": 0.0},
        ]
        events = capital_changes_from_dividends_and_rights([], rights)
        assert events[0].rights_price == pytest.approx(8.85)

    def test_snake_case_columns_also_accepted(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        dividends = [{"ex_dividend_date": "2020-07-01", "cash_dividend_per_10": 3.0}]
        rights = [
            {"ex_dividend_date": "2020-07-01", "rights_ratio_per_10": 2.0, "rights_price": 5.0}
        ]
        events = capital_changes_from_dividends_and_rights(dividends, rights)
        assert events[0].dividend == pytest.approx(3.0)
        assert events[0].rights_ratio == pytest.approx(2.0)

    def test_date_with_time_suffix_normalized(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends_and_rights

        dividends = [{"EX_DIVIDEND_DATE": "2020-07-01 00:00:00", "PRETAX_BONUS_RMB": 1.0}]
        events = capital_changes_from_dividends_and_rights(dividends)
        assert events[0].date == "2020-07-01"

    def test_dividends_only_entry_is_a_thin_shell(self) -> None:
        """``capital_changes_from_dividends`` 只是合并且函数的特例，不是第二份实现。

        两条独立实现迟早会在"同日多行要不要并成一条"上分叉，而分叉出来的那半边
        不会有人发现——所以这里把两条路径的输出钉成同一个值。
        """
        from atst.domain.finance import (
            capital_changes_from_dividends,
            capital_changes_from_dividends_and_rights,
        )

        rows = [
            {"EX_DIVIDEND_DATE": "2020-07-01", "PRETAX_BONUS_RMB": 1.0},
            {"EX_DIVIDEND_DATE": "2020-07-01", "PRETAX_BONUS_RMB": 2.0},
        ]
        only = capital_changes_from_dividends(rows, code="600519")
        both = capital_changes_from_dividends_and_rights(rows, [], code="600519")
        assert [e.to_dict() for e in only] == [e.to_dict() for e in both]
        #: 同日两行合成一条，派息相加（不是两条事件各乘一次）。
        assert len(only) == 1
        assert only[0].dividend == pytest.approx(3.0)

    def test_dividends_only_entry_zeroes_rights(self) -> None:
        from atst.domain.finance import capital_changes_from_dividends

        rows = [{"EX_DIVIDEND_DATE": "2020-07-01", "PRETAX_BONUS_RMB": 1.0}]
        ev = capital_changes_from_dividends(rows)[0]
        assert ev.rights_ratio == pytest.approx(0.0)
        assert ev.rights_price == pytest.approx(0.0)
