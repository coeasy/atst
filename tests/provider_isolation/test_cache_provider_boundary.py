from tstdx.provider_guard import (
    ProviderExecutionIdentity,
    validate_execution_identity,
)


def test_provider_identity_requires_exact_match() -> None:
    identity = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )

    validate_execution_identity(identity, identity)


def test_provider_identity_rejects_cross_provider_execution() -> None:
    requested = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    executing = ProviderExecutionIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
    )

    try:
        validate_execution_identity(requested, executing)
    except RuntimeError:
        return

    raise AssertionError("cross-provider execution must be rejected")
