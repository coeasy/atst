from __future__ import annotations

import pytest

from tstdx.client_core import _quote_body, split_symbol
from tstdx.errors import ParseError


def test_quote_body_preserves_verified_sz_sh_reverse_bytes() -> None:
    assert _quote_body("000001", 0)[:2] == bytes([0x01, 0x01])
    assert _quote_body("600519", 1)[:2] == bytes([0x01, 0x00])


def test_bj_symbol_market_is_not_silently_clamped_for_unverified_quote_families() -> None:
    market, code = split_symbol("bj430047")

    assert market == 2
    with pytest.raises(ParseError, match="仅验证 0/1"):
        _quote_body(code, market)


@pytest.mark.parametrize("market", [-1, 2, 99, True, 1.0, "1"])
def test_quote_body_rejects_unverified_or_coercible_market_identity(market) -> None:
    with pytest.raises(ParseError):
        _quote_body("600519", market)  # type: ignore[arg-type]
