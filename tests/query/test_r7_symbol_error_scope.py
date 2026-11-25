# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""R-7: market routing only absorbs expected symbol-parse failures."""

from __future__ import annotations

import pytest

from atst.errors import SymbolError
from atst.query import QuerySpec
from atst.query import spec as spec_module


def test_unexpected_parse_error_is_not_swallowed(monkeypatch) -> None:
    def boom(raw, **kwargs):
        raise RuntimeError("unexpected parser fault")

    monkeypatch.setattr(spec_module, "parse_symbol", boom)
    query = QuerySpec.build("quotes", symbols="sh600000")

    with pytest.raises(RuntimeError, match="unexpected parser fault"):
        query.normalized()


def test_symbol_error_still_degrades_to_no_market_hint(monkeypatch) -> None:
    def bad_symbol(raw, **kwargs):
        raise SymbolError("bad symbol")

    monkeypatch.setattr(spec_module, "parse_symbol", bad_symbol)
    query = QuerySpec.build("quotes", symbols="sh600000")

    normalized = query.normalized()

    assert normalized.capability == "quotes"
