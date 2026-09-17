from tstdx.runtime_identity import (
    RuntimeCacheIdentity,
    RuntimeExecutionIdentity,
)


def test_runtime_cache_identity_keeps_provider_boundary() -> None:
    tdx = RuntimeCacheIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="same",
    )
    eastmoney = RuntimeCacheIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
        fingerprint="same",
    )

    assert tdx != eastmoney


def test_runtime_execution_identity_is_provider_specific() -> None:
    identity = RuntimeExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="quotes",
    )

    assert identity.provider == "tdx"
