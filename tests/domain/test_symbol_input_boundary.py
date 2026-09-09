from __future__ import annotations

import pytest

from tstdx.domain.symbol import clear_symbol_cache, normalize_symbol, parse_symbol, to_tdx_market
from tstdx.errors import SymbolError


@pytest.mark.parametrize("raw", [123, [], {}, {"symbol": "600519"}, b"600519"])
def test_parse_symbol_rejects_non_string_input_before_cache(raw) -> None:
    with pytest.raises(SymbolError, match="证券代码必须是字符串"):
        parse_symbol(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize("market", [1, True, [], {}])
def test_parse_symbol_rejects_non_string_market_before_cache(market) -> None:
    with pytest.raises(SymbolError, match="market 必须是字符串或 None"):
        parse_symbol("600519", market=market)  # type: ignore[arg-type]


def test_public_helpers_inherit_symbol_type_boundary() -> None:
    with pytest.raises(SymbolError):
        normalize_symbol([])  # type: ignore[arg-type]
    with pytest.raises(SymbolError):
        to_tdx_market({}, market=None)  # type: ignore[arg-type]


def test_clear_symbol_cache_still_clears_internal_cache_without_changing_public_api() -> None:
    first = parse_symbol("600519")
    second = parse_symbol("600519")
    assert first is second

    clear_symbol_cache()
    third = parse_symbol("600519")

    assert third == first
    assert third is not first
