from tstdx.runtime.identity import (
    RuntimeCacheIdentity,
    RuntimeExecutionIdentity,
)


def test_runtime_plan_identity_requires_same_provider() -> None:
    cache = RuntimeCacheIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="bars-request",
    )
    execution = RuntimeExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )

    assert cache.provider == execution.provider


def test_runtime_plan_identity_detects_provider_boundary() -> None:
    cache = RuntimeCacheIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
        fingerprint="bars-request",
    )
    execution = RuntimeExecutionIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
    )

    assert cache.provider != execution.provider
