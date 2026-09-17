"""Guards for Provider Isolation execution rules.

These tests document the invariant that execution identity is never silently
changed after planning.
"""

from __future__ import annotations

import pytest

from tstdx.provider_contract import (
    ProviderExecutionContract,
    ProviderIdentity,
)


def test_execution_contract_requires_exact_provider_identity() -> None:
    contract = ProviderExecutionContract(
        identity=ProviderIdentity(
            provider="tdx",
            channel="quotation",
        ),
        capability="bars",
    )

    assert contract.identity.provider == "tdx"
    assert contract.identity.channel == "quotation"
    assert contract.capability == "bars"


def test_provider_switch_must_not_be_implicit() -> None:
    contract = ProviderExecutionContract(
        identity=ProviderIdentity(
            provider="eastmoney",
            channel="kline",
        ),
        capability="bars",
    )

    with pytest.raises(ValueError):
        contract.assert_identity(
            ProviderIdentity(
                provider="tdx",
                channel="quotation",
            )
        )
