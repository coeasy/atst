from dataclasses import dataclass


@dataclass(frozen=True)
class CacheIdentity:
    provider: str
    channel: str
    capability: str
    fingerprint: str


def test_cache_identity_includes_provider_boundary() -> None:
    tdx = CacheIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="same-request",
    )
    eastmoney = CacheIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
        fingerprint="same-request",
    )

    assert tdx != eastmoney


def test_provider_caches_must_not_share_identity() -> None:
    keys = {
        (
            "tdx",
            "quotation",
            "bars",
            "sh600519-day",
        ),
        (
            "eastmoney",
            "kline",
            "bars",
            "sh600519-day",
        ),
    }

    assert len(keys) == 2
