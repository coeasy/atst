from tstdx.catalog.provider_guard import (
    ProviderExecutionIdentity,
    validate_execution_identity,
)


class FakeBinding:
    def __init__(self, provider: str, channel: str, capability: str) -> None:
        self.identity = ProviderExecutionIdentity(
            provider=provider,
            channel=channel,
            capability=capability,
        )


def test_direct_binding_identity_matches_plan() -> None:
    plan_identity = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    binding = FakeBinding("tdx", "quotation", "bars")

    validate_execution_identity(plan_identity, binding.identity)


def test_direct_binding_identity_blocks_channel_switch() -> None:
    plan_identity = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    binding = FakeBinding("tdx", "kline", "bars")

    try:
        validate_execution_identity(plan_identity, binding.identity)
    except RuntimeError:
        return

    raise AssertionError("channel switch must be rejected")


def test_direct_binding_identity_blocks_capability_switch() -> None:
    plan_identity = ProviderExecutionIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    binding = FakeBinding("tdx", "quotation", "quotes")

    try:
        validate_execution_identity(plan_identity, binding.identity)
    except RuntimeError:
        return

    raise AssertionError("capability switch must be rejected")
