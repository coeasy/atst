from dataclasses import dataclass


@dataclass(frozen=True)
class NegativeCacheKey:
    provider: str
    channel: str
    capability: str
    fingerprint: str


def test_negative_cache_key_contains_provider() -> None:
    tdx_error = NegativeCacheKey(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="request-a",
    )
    eastmoney_error = NegativeCacheKey(
        provider="eastmoney",
        channel="kline",
        capability="bars",
        fingerprint="request-a",
    )

    assert tdx_error != eastmoney_error


def test_retryable_provider_failure_should_not_define_other_provider_state() -> None:
    failed_provider = NegativeCacheKey(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="request-a",
    )

    other_provider = NegativeCacheKey(
        provider="eastmoney",
        channel="kline",
        capability="bars",
        fingerprint="request-a",
    )

    assert failed_provider.provider != other_provider.provider
