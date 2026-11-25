# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""R-8: typed queries forward every declared field, including defaults.

The original R-8 sketch wanted "only fields the caller set", but capability
contracts mark ``symbol`` / ``codes`` / ``page`` / ``size`` as required while
their dataclass defaults are empty. Dropping default-valued fields made the
kernel compiler reject the query as ``missing a required argument`` (v14
``contract_audit --ci`` caught it). Default-value semantics belong to the
capability contract, so this layer forwards the full field set.
"""

from __future__ import annotations

from atst.typed_query import (
    CapabilityQuery,
    FundRankQuery,
    FuturesRealtimeQuery,
    NewsQuery,
    call_payload_from_typed,
)


def test_default_field_values_are_still_emitted() -> None:
    # Required-by-contract arguments must reach the provider even at their
    # dataclass default; otherwise the kernel cannot bind the call.
    assert call_payload_from_typed(FundRankQuery()) == {"fund_type": 0}
    assert call_payload_from_typed(FuturesRealtimeQuery()) == {"quote_id": ""}
    assert call_payload_from_typed(NewsQuery()) == {"page": 1, "size": 30}


def test_non_default_fields_and_options_are_emitted() -> None:
    assert call_payload_from_typed(FundRankQuery(fund_type=5)) == {"fund_type": 5}
    assert call_payload_from_typed(FuturesRealtimeQuery(quote_id="IF2509")) == {
        "quote_id": "IF2509"
    }
    assert call_payload_from_typed(NewsQuery(page=2, size=10)) == {"page": 2, "size": 10}


def test_options_are_preserved_and_explicit_fields_win() -> None:
    query = CapabilityQuery(capability="quotes", options={"page": 3})
    assert call_payload_from_typed(query) == {"page": 3}

    query = FundRankQuery(fund_type=5, options={"fund_type": 7})
    assert call_payload_from_typed(query) == {"fund_type": 5}
