from __future__ import annotations

import pytest

from tstdx.client import TdxClient
from tstdx.client_core import _standard_market_id, split_symbol
from tstdx.domain.symbol import to_tdx_market
from tstdx.errors import ParseError


class _NoIoPool:
    def request(self, *_args, **_kwargs):
        raise AssertionError("unverified BJ symbol request must fail before transport I/O")


def test_domain_keeps_bj_identity_but_client_symbol_protocol_does_not_extrapolate_it() -> None:
    assert to_tdx_market("430047") == (2, "430047")
    assert _standard_market_id(2) == 2
    assert _standard_market_id("bj") == 2

    with pytest.raises(ParseError, match="当前只允许 SZ/SH") as exc_info:
        split_symbol("430047")

    assert exc_info.value.context["market"] == 2
    assert exc_info.value.context["verified_markets"] == [0, 1]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("bars", ("430047",)),
        ("finance_info", ("430047",)),
        ("capital_changes", ("430047",)),
        ("minute_today", ("430047",)),
        ("trade_today", ("430047",)),
    ],
)
def test_symbol_based_bj_client_paths_fail_before_io(method: str, args: tuple[str, ...]) -> None:
    client = TdxClient(pool=_NoIoPool())

    with pytest.raises(ParseError):
        getattr(client, method)(*args)
