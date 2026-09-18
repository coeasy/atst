"""Provider isolation contract tests.

These tests lock the architectural rule:
- Provider identity is explicit.
- Provider execution targets cannot silently switch source.
- Cache/provenance boundaries remain provider scoped.
"""

from __future__ import annotations

import pytest

from tstdx.catalog.provider_contract import (
    ProviderCapabilityContract,
    ProviderExecutionContract,
    ProviderIdentity,
)


def test_provider_identity_requires_explicit_source() -> None:
    identity = ProviderIdentity(provider="tdx", channel="quotation")

    assert identity.provider == "tdx"
    assert identity.channel == "quotation"


def test_provider_execution_contract_keeps_single_provider() -> None:
    execution = ProviderExecutionContract(
        identity=ProviderIdentity("tdx", "quotation"),
        capability="bars",
    )

    assert execution.identity.provider == "tdx"
    assert execution.capability == "bars"


def test_provider_capability_contract_isolated() -> None:
    contract = ProviderCapabilityContract(
        provider="eastmoney",
        capabilities=frozenset({"quotes", "bars"}),
    )

    assert contract.supports("quotes")
    assert not contract.supports("trades")


def test_provider_switch_must_not_be_implicit() -> None:
    execution = ProviderExecutionContract(
        identity=ProviderIdentity("tdx", "quotation"),
        capability="bars",
    )

    with pytest.raises(ValueError):
        execution.assert_provider("eastmoney")
