from tstdx.provider_guard import (
    ProviderExecutionIdentity,
    validate_execution_identity,
)


def test_result_provider_identity_must_match_execution_identity() -> None:
    requested = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    returned = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )

    validate_execution_identity(requested, returned)


def test_result_provider_identity_cannot_be_rewritten() -> None:
    requested = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    returned = ProviderExecutionIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
    )

    try:
        validate_execution_identity(requested, returned)
    except RuntimeError:
        return

    raise AssertionError("provenance identity rewrite must be rejected")
