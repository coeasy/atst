# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Pure-function unit tests for the ``_t_bars`` pagination state machine.

``_BarsPageCursor`` / ``_absorb_bars_page`` / ``_bars_truncation_diagnosis`` are
white-box helpers extracted from the template loop.  They take only in-memory
values, mutate an explicit state object and return a decision -- no socket, no
warning emission, no network.  These tests pin that behaviour so the
"pure-functionalization deferred" item is resolved by direct verification (the
I/O-owning generator body is out of scope here).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from atst.client import _templates_standard as ts
from atst.client._templates_standard import (
    _absorb_bars_page,
    _bars_truncation_diagnosis,
    _BarsPageCursor,
)
from atst.diagnostics import WarningCode


def _row(datetime: str) -> dict[str, Any]:
    return {
        "datetime": datetime,
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10,
        "amount": 15.0,
    }


def _bad_row() -> dict[str, Any]:
    """A date that looks like a date but is not a real calendar day."""
    return _row("8047-92-78")


class TestAbsorbBarsPage:
    def test_cursor_advances_across_full_pages(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=5)
        page1 = [_row("2026-01-01"), _row("2026-01-02"), _row("2026-01-03")]
        assert _absorb_bars_page(state, page1, 3, meta={}) is None
        assert state.offset == 3
        assert state.remaining == 2
        assert len(state.bars) == 3
        assert state.seen == {"2026-01-01", "2026-01-02", "2026-01-03"}

        page2 = [_row("2026-01-04"), _row("2026-01-05")]
        assert _absorb_bars_page(state, page2, 2, meta={}) is None
        assert state.offset == 5
        assert state.remaining == 0
        assert len(state.bars) == 5

    def test_short_page_absorbs_rows_but_does_not_advance_cursor(self) -> None:
        state = _BarsPageCursor(offset=4, remaining=10)
        assert (
            _absorb_bars_page(state, [_row("2026-01-01"), _row("2026-01-02")], 5, meta={})
            == "exhausted_short"
        )
        assert len(state.bars) == 2
        # The short-page decision returns before the cursor advance.
        assert state.offset == 4
        assert state.remaining == 10
        assert state.seen == {"2026-01-01", "2026-01-02"}

    def test_empty_secondary_page_is_exhausted_without_first_page_flags(self) -> None:
        state = _BarsPageCursor(offset=5, remaining=3, seen={"2026-01-01"})
        assert _absorb_bars_page(state, [], 3, meta={"declared_count": 800}) == "exhausted_empty"
        assert state.empty_first_page is False
        assert state.first_page_declared is None

    @pytest.mark.parametrize("declared", [800, 0, None])
    def test_empty_first_page_records_server_declared_count(self, declared: int | None) -> None:
        state = _BarsPageCursor(offset=0, remaining=5)
        assert _absorb_bars_page(state, [], 5, meta={"declared_count": declared}) == (
            "exhausted_empty"
        )
        assert state.empty_first_page is True
        assert state.first_page_declared == declared

    def test_all_duplicate_page_is_drifted(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=3)
        assert _absorb_bars_page(state, [_row("2026-01-01")], 1, meta={}) is None
        assert _absorb_bars_page(state, [_row("2026-01-01")], 1, meta={}) == "drifted"
        assert state.drifted is True
        assert len(state.bars) == 1  # no new bar absorbed on the duplicate page

    def test_majority_violating_page_is_unusable(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=5)
        rows = [_bad_row(), _bad_row(), _bad_row(), _row("2026-01-01")]
        assert _absorb_bars_page(state, rows, 4, meta={}) == "unusable_page"
        assert state.unusable_page is True
        assert state.page_rows == 4
        assert state.offenders == 3
        assert state.bars == []

    def test_exact_n_leaves_remaining_at_zero(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=3)
        rows = [_row("2026-01-01"), _row("2026-01-02"), _row("2026-01-03")]
        assert _absorb_bars_page(state, rows, 3, meta={}) is None
        assert state.remaining == 0
        assert state.offset == 3

    def test_over_n_underflows_remaining_and_advances_by_raw_rows(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=2)
        rows = [_row("2026-01-01"), _row("2026-01-02"), _row("2026-01-03")]
        assert _absorb_bars_page(state, rows, 2, meta={}) is None
        assert state.remaining == -1
        assert state.offset == 3  # advances by len(raw_rows), not the requested page

    def test_duplicates_within_one_page_are_not_deduplicated(self) -> None:
        # The seen-set is updated only after the whole page's freshness filter
        # runs, so two identical datetimes in the SAME page both count as fresh;
        # dedup applies across pages only.
        state = _BarsPageCursor(offset=0, remaining=4)
        rows = [
            _row("2026-01-01"),
            _row("2026-01-01"),
            _row("2026-01-02"),
            _row("2026-01-03"),
        ]
        assert _absorb_bars_page(state, rows, 4, meta={}) is None
        assert state.offset == 4
        assert state.remaining == 0
        assert len(state.bars) == 4
        assert state.seen == {"2026-01-01", "2026-01-02", "2026-01-03"}


class TestBarsTruncationDiagnosis:
    def test_full_count_and_no_flags_is_silent(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=0)
        state.bars.extend([_row("2026-01-01"), _row("2026-01-02")])
        assert _bars_truncation_diagnosis(state, "sh600000", "day", 2) is None

    def test_anchor_drift_truncation(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=3)
        state.bars.append(_row("2026-01-01"))
        state.drifted = True
        code, msg, context = _bars_truncation_diagnosis(state, "sh600000", "day", 5)
        assert code is WarningCode.BARS_ANCHOR_DRIFT
        assert "锚点漂移" in msg
        assert context == {"symbol": "sh600000", "returned": 1, "requested": 5}

    @pytest.mark.parametrize(
        ("declared", "fragment"),
        [
            (800, "空桩"),
            (0, "无此周期历史"),
            (None, "声明数未知"),
        ],
    )
    def test_empty_first_page_cases(self, declared: int | None, fragment: str) -> None:
        state = _BarsPageCursor(offset=0, remaining=5)
        state.empty_first_page = True
        state.first_page_declared = declared
        code, msg, context = _bars_truncation_diagnosis(state, "sh600000", "day", 5)
        assert code is WarningCode.BARS_EMPTY_FIRST_PAGE
        assert fragment in msg
        assert context == {
            "symbol": "sh600000",
            "returned": 0,
            "requested": 5,
            "declared": declared,
        }

    def test_unusable_page_reports_offenders_and_rows(self) -> None:
        state = _BarsPageCursor(offset=0, remaining=0)
        state.unusable_page = True
        state.page_rows = 5
        state.offenders = 4
        state.bars.append(_row("2026-01-01"))
        code, msg, context = _bars_truncation_diagnosis(state, "sz399999", "day", 5)
        assert code is WarningCode.BARS_PAGE_UNUSABLE
        assert "4/5" in msg
        assert context == {
            "symbol": "sz399999",
            "period": "day",
            "returned": 1,
            "requested": 5,
        }


def test_pagination_helpers_do_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helpers are synchronous, in-memory functions: no request op, no warning emission."""

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("pagination helper performed I/O")

    monkeypatch.setattr(ts, "_op_req", explode)
    monkeypatch.setattr(ts, "_op_call", explode)
    monkeypatch.setattr(ts, "record_warning", explode)

    assert not inspect.iscoroutinefunction(_absorb_bars_page)
    assert not inspect.iscoroutinefunction(_bars_truncation_diagnosis)

    state = _BarsPageCursor(offset=0, remaining=2)
    assert _absorb_bars_page(state, [_row("2026-01-01"), _row("2026-01-02")], 2, meta={}) is None
    assert _bars_truncation_diagnosis(state, "sh600000", "day", 2) is None
