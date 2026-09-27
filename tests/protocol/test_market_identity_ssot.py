from __future__ import annotations

import pytest

from atst.domain.symbol import to_tdx_market
from atst.errors import ParseError
from atst.protocol.parsers.std7709 import (
    build_realtime_quote_body,
    infer_market,
    quote_request_market,
)


def test_protocol_market_inference_matches_domain_symbol_ssot_for_ambiguous_000001() -> None:
    domain_market, code = to_tdx_market("000001")

    assert code == "000001"
    assert domain_market == 0
    assert infer_market("000001") == domain_market
    assert build_realtime_quote_body("000001")[:2] == bytes([0x01, 0x01])


def test_bj_symbol_identity_is_known_but_realtime_request_byte_is_not_guessed() -> None:
    domain_market, code = to_tdx_market("430047")

    assert code == "430047"
    assert domain_market == 2
    assert infer_market("430047") == domain_market
    with pytest.raises(ParseError, match="北交所.*golden") as exc_info:
        build_realtime_quote_body("430047")
    assert exc_info.value.context["market"] == 2
    assert exc_info.value.context["verified_markets"] == [0, 1]


def test_explicit_sh_market_remains_available_for_ambiguous_index_code() -> None:
    assert build_realtime_quote_body("000001", market=1)[:2] == bytes([0x01, 0x00])


@pytest.mark.parametrize("market", [-1, 2, 3, 99, True, 1.0, "1"])
def test_realtime_quote_market_rejects_unverified_or_coercible_identity(market) -> None:
    with pytest.raises(ParseError):
        quote_request_market(market)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "code",
    ["", "12345", "1234567", "ABCDEF", "12 345", "１２３４５６"],
)
def test_realtime_quote_body_requires_exact_six_ascii_digits(code: str) -> None:
    with pytest.raises(ParseError, match="6 位 ASCII 数字"):
        build_realtime_quote_body(code)
