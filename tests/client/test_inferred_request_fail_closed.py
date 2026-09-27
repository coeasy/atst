from __future__ import annotations

import pytest

from atst.client import ExMarketClient, GoodsClient, MacClient
from atst.client.core import _bars_body, _quote_body
from atst.errors import NotImplementedFeature, SymbolError


class _NoIoPool:
    def request(self, *_args, **_kwargs):
        raise AssertionError("unverified request body must fail before transport I/O")


def test_inferred_bars_builder_never_emits_old_standard_body() -> None:
    with pytest.raises(NotImplementedFeature, match="golden") as exc_info:
        _bars_body(1, "600000", 4, 0, 10)

    assert exc_info.value.context["commands"] == ["0x0104", "0x0202"]
    assert exc_info.value.context["expected_inferred_length"] == 12


def test_inferred_quote_builder_never_emits_reversed_market_body() -> None:
    with pytest.raises(NotImplementedFeature, match="golden") as exc_info:
        _quote_body("600000", 1)

    assert exc_info.value.context["commands"] == ["0x0105", "0x0203", "0x1301"]


@pytest.mark.parametrize(
    ("client_type", "method", "args"),
    [
        (ExMarketClient, "ex_bars", ("600000",)),
        (ExMarketClient, "ex_quote", ("600000",)),
        (GoodsClient, "goods_bars", ("600000",)),
        (GoodsClient, "goods_quote", ("600000",)),
        (MacClient, "mac_quote", ("600000",)),
    ],
)
def test_public_inferred_market_requests_fail_before_io(
    client_type,
    method: str,
    args: tuple[str, ...],
) -> None:
    client = client_type(pool=_NoIoPool())

    with pytest.raises(NotImplementedFeature):
        getattr(client, method)(*args)


def test_extended_hk_identity_cannot_fall_back_to_standard_tdx_market() -> None:
    client = ExMarketClient(pool=_NoIoPool())

    with pytest.raises(SymbolError):
        client.ex_quote("hk00700")


def test_goods_alphanumeric_identity_cannot_be_coerced_to_share_symbol() -> None:
    client = GoodsClient(pool=_NoIoPool())

    with pytest.raises(SymbolError):
        client.goods_quote("rb0000")
