from dataclasses import dataclass


@dataclass(frozen=True)
class SingleFlightKey:
    provider: str
    channel: str
    capability: str
    fingerprint: str


def test_singleflight_key_contains_provider_identity() -> None:
    tdx = SingleFlightKey(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="same-fingerprint",
    )
    eastmoney = SingleFlightKey(
        provider="eastmoney",
        channel="kline",
        capability="bars",
        fingerprint="same-fingerprint",
    )

    assert tdx != eastmoney


def test_same_provider_can_share_singleflight_identity() -> None:
    first = SingleFlightKey(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="same-fingerprint",
    )
    second = SingleFlightKey(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="same-fingerprint",
    )

    assert first == second
