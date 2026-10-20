"""日期区间裁剪：结果盖不住区间起点时必须说话（2026-10-20 修复回归）。

协议侧只有"从最新往回数 ``count`` 根"这一种游标，没有服务端日期区间参数，
所以 ``start_date``/``end_date`` 是在取回的**那一页**上裁的。旧实现把
:data:`~atst.diagnostics.WarningCode.BARS_RANGE_UNCOVERED` 告警挂在"裁剪后结果非空"
上，于是区间**完全落在取回窗口之外**时（``kept`` 为空、``days`` 却是真日期）静默
返回空列表、零告警——调用方无从分辨"这些日期没有行情"与"这一段根本没取回来"。
"""

from __future__ import annotations

import pytest

from atst.diagnostics import WarningCode, warning_sink
from atst.domain.models import Bar
from atst.query import QueryPlanner, QuerySpec
from atst.runtime.executor import DirectProviderExecutor


class _StubSource:
    """最小 Web 源替身：只实现执行器用到的出口。"""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def fetch_bars(self, symbol, **kwargs):  # noqa: ARG002 - 替身不校验入参
        return list(self._rows)

    def close(self):
        self.closed = True


def _bars(days: list[str]) -> list[Bar]:
    return [
        Bar(datetime=day, open=1.0, high=2.0, low=0.5, close=1.5, volume=100)
        for day in days
    ]


def _run(monkeypatch: pytest.MonkeyPatch, rows: list[Bar], period: str = "day", **spec_kw):
    source = _StubSource(rows)
    monkeypatch.setattr("atst.web.create_source", lambda *a, **k: source)
    plan = QueryPlanner().compile(
        QuerySpec.build("bars", symbols="sh600519", provider="tencent", period=period, **spec_kw)
    )
    return DirectProviderExecutor(timeout=1.0).execute(plan), source


class TestBarsRangeUncovered:
    def test_interval_entirely_before_window_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """区间完全落在取回窗口之前 → 空结果 + bars_range_uncovered（不得静默）。"""

        rows = _bars(["2026-09-01", "2026-09-02", "2026-09-03"])
        result, source = _run(
            monkeypatch, rows, count=30, start_date="1999-01-01", end_date="1999-01-31"
        )

        assert result.data == []
        assert [w.code for w in result.meta.warnings] == [WarningCode.BARS_RANGE_UNCOVERED]
        assert source.closed is True

    def test_interval_covered_no_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """取回窗口触及区间起点之前 → 区间完整，不得误报。"""

        rows = _bars(["2026-08-01", "2026-08-02", "2026-09-01"])
        result, _ = _run(
            monkeypatch, rows, count=30, start_date="2026-09-01", end_date="2026-09-30"
        )

        assert [b["datetime"] for b in result.data] == ["2026-09-01"]
        assert result.meta.warnings == ()

    def test_future_interval_no_false_positive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未来区间只是"这些日期没有行情"，不是截断——不得误报。"""

        rows = _bars(["2026-09-01", "2026-09-02"])
        result, _ = _run(
            monkeypatch, rows, count=30, start_date="2030-01-01", end_date="2030-01-31"
        )

        assert result.data == []
        assert result.meta.warnings == ()


def _apply_range(rows: list[Bar], period: str, **spec_kw):
    """直接量 ``_apply_bar_range`` 本身。

    计划的 Provider 取 ``tdx`` 是为了让 ``year``/``season`` 过得了 Provider 周期门
    （Web 的 ``kline`` 只服务 ``day``/``month``/``week``）——本判据本身与 Provider 无关，
    量的只是"区间裁剪 + 盖不住时说话"这一段。
    """
    plan = QueryPlanner().compile(
        QuerySpec.build("bars", symbols="sh600519", provider="tdx", period=period, **spec_kw)
    )
    with warning_sink() as collected:
        out = DirectProviderExecutor(timeout=1.0)._apply_bar_range(plan, rows)
    return out, collected


class TestBarsRangeInteriorEmpty:
    """粗周期的 bar 以「周期末交易日」为 datetime：窗口落在周期内部时必然裁空（2026-10 补）。"""

    def test_year_window_inside_span_warns(self) -> None:
        """实测形状：``year`` 取 2026-09 区间回空——2026 的 bar 挂着 2026-10-20，
        更早的 bar 都在区间起点之前，两侧都有 bar 却一根都不命中。"""

        rows = _bars(["2025-12-31", "2026-10-20"])
        kept, collected = _apply_range(
            rows, "year", count=30, start_date="2026-09-01", end_date="2026-09-30"
        )

        assert kept == []
        assert [w.code for w in collected] == [WarningCode.BARS_RANGE_INTERIOR_EMPTY]

    @pytest.mark.parametrize("period", ["week", "month", "season"])
    def test_every_coarse_period_warns(self, period: str) -> None:
        """周/月/季是同一形状，不是只给 year 开的口子。"""

        rows = _bars(["2026-08-31", "2026-10-20"])
        kept, collected = _apply_range(
            rows, period, count=30, start_date="2026-09-01", end_date="2026-09-10"
        )

        assert kept == []
        assert [w.code for w in collected] == [WarningCode.BARS_RANGE_INTERIOR_EMPTY]

    def test_day_period_stays_silent(self) -> None:
        """``day`` 的同一形状是休市日窗口（"这些日期没有行情"）——不得误报。"""

        rows = _bars(["2026-09-05", "2026-10-08"])
        kept, collected = _apply_range(
            rows, "day", count=30, start_date="2026-09-30", end_date="2026-10-02"
        )

        assert kept == []
        assert collected == []

    def test_future_interval_for_year_stays_silent(self) -> None:
        """粗周期的未来区间落在跨度之外（没有更晚的 bar），仍属"这些日期没有行情"。"""

        rows = _bars(["2025-12-31", "2026-10-20"])
        kept, collected = _apply_range(
            rows, "year", count=30, start_date="2030-01-01", end_date="2030-12-31"
        )

        assert kept == []
        assert collected == []
