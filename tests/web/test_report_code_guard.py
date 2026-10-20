# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""东财 datacenter 报表的撞码守卫（2026-10-13 实测回归的固化）。

这些报表按 **6 位 ``SECURITY_CODE``** 过滤，市场前缀在 filter 里必然丢掉。于是
``sh000001``（上证指数）与 ``sz000001``（平安银行）撞码。实测::

    dividend_history('sh000001') → 平安银行 20 条分红
    margin('sh000001')          → 平安银行 20 条两融
    holder_num('sh000001')      → 平安银行 1 条

后果不只是"多几个字段"：``adjusted_bars('sh000001', 'hfq')`` 拿平安银行的分红给
上证指数复权，收盘价从 3797 变成 434924。

守卫是 :func:`atst.web.corporate.report_code_for`：指数返回 ``None``，调用方按
**空集**处理。这是事实陈述（指数没有公司行为），不是容错。判据与 ``0x052D`` 指数位
共用 :func:`atst.universe.classify_with_index` 的段表，不另抄一套。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.web.corporate import EastmoneyDataCenterSource, report_code_for
from atst.web.eastmoney.adapters import EastmoneyMarginSource

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# report_code_for：指数不在这些报表的覆盖范围内
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "symbol",
    ["sh000001", "sh000300", "sh000688", "sz399001", "sz399006", "sz399905"],
)
def test_index_symbols_have_no_report_code(symbol: str) -> None:
    assert report_code_for(symbol) is None


@pytest.mark.parametrize(
    ("symbol", "code"),
    [
        ("sh600519", "600519"),
        ("sz000001", "000001"),
        ("sh510300", "510300"),  # ETF 有份额/净值报表
        ("sh110031", "110031"),  # 可转债
        ("sh900901", "900901"),  # B 股
        ("bj920000", "920000"),  # 北交所
        ("600519", "600519"),  # 裸 6 位
    ],
)
def test_non_index_symbols_keep_their_six_digit_code(symbol: str, code: str) -> None:
    assert report_code_for(symbol) == code


def test_the_collision_pair_is_separated() -> None:
    """``sh000001`` 与 ``sz000001`` 在报表里本来是同一个 6 位码——守卫必须只挡前者。"""
    assert report_code_for("sh000001") is None
    assert report_code_for("sz000001") == "000001"


# --------------------------------------------------------------------------- #
# choke point：fetch_rows_by_code 不再发起任何请求
# --------------------------------------------------------------------------- #


class _ExplodingSource(EastmoneyDataCenterSource):
    """任何真实取数都会炸——指数路径必须在到达网络之前就返回空集。"""

    def fetch_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError(f"指数不该走到取数这一步：{kwargs}")


@pytest.mark.parametrize("symbol", ["sh000001", "sh000300", "sz399001", "sz399006"])
def test_fetch_rows_by_code_short_circuits_for_index(symbol: str) -> None:
    assert _ExplodingSource().fetch_rows_by_code(symbol) == []


@pytest.mark.parametrize("symbol", ["sh000001", "sh000300", "sz399001", "sz399006"])
def test_fetch_margin_short_circuits_for_index(symbol: str) -> None:
    """两融是个独立方法，但走同一个 :func:`report_code_for` 守卫。"""
    source = EastmoneyMarginSource()

    def _explode(**kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError(f"指数不该走到取数这一步：{kwargs}")

    source.fetch_rows = _explode  # type: ignore[method-assign]
    assert source.fetch_margin(symbol) == []
    assert source.fetch_margin(symbol, days=20, all_pages=True) == []


def test_fetch_rows_by_code_still_queries_stocks_with_the_bare_code() -> None:
    """守卫不能顺手把股票也挡了——它只对指数生效。"""
    seen: list[dict[str, Any]] = []

    class _Recording(EastmoneyDataCenterSource):
        def fetch_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
            seen.append(kwargs)
            return [{"SECURITY_CODE": "600519"}]

    rows = _Recording().fetch_rows_by_code("sh600519", report="RPT_SHAREBONUS_DET")
    assert rows == [{"SECURITY_CODE": "600519"}]
    assert seen[0]["filters"] == ['SECURITY_CODE="600519"']
    assert seen[0]["report"] == "RPT_SHAREBONUS_DET"
