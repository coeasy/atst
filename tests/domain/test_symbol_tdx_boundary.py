from __future__ import annotations

import pytest

from tstdx.client import TdxClient
from tstdx.domain.symbol import normalize_symbol, parse_symbol, to_tdx_market
from tstdx.errors import SymbolError


def test_hk_and_us_remain_valid_canonical_symbols() -> None:
    assert normalize_symbol("hk00700") == "hk00700"
    assert normalize_symbol("usAAPL") == "usAAPL"


@pytest.mark.parametrize("symbol", ["hk00700", "00700", "usAAPL"])
def test_non_tdx_markets_cannot_be_coerced_to_sz_market_zero(symbol: str) -> None:
    with pytest.raises(SymbolError, match="不属于 TDX"):
        to_tdx_market(symbol)


def test_tdx_client_rejects_hk_symbol_before_pool_io() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(SymbolError, match="不属于 TDX"):
        client.bars("hk00700", count=1)


def test_explicit_market_cannot_conflict_with_written_symbol_market() -> None:
    with pytest.raises(SymbolError, match="冲突"):
        parse_symbol("sh600000", market="sz")


def test_explicit_non_us_market_cannot_override_us_symbol() -> None:
    with pytest.raises(SymbolError, match="冲突"):
        parse_symbol("usAAPL", market="sh")


def test_explicit_hk_market_requires_five_digit_code() -> None:
    with pytest.raises(SymbolError, match="5 位"):
        parse_symbol("600000", market="hk")
