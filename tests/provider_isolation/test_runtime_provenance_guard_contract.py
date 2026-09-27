from types import SimpleNamespace

import pytest

from atst.runtime.identity import RuntimeExecutionIdentity
from atst.runtime.provenance import (
    RuntimeProvenanceMismatchError,
    validate_runtime_provenance,
)


def test_runtime_provenance_accepts_matching_identity() -> None:
    result = SimpleNamespace(
        meta=SimpleNamespace(
            provenance=SimpleNamespace(
                provider="tdx",
                channel="quotation",
                capability="bars",
            )
        )
    )

    validate_runtime_provenance(
        RuntimeExecutionIdentity(
            provider="tdx",
            channel="quotation",
            capability="bars",
        ),
        result,
    )


def test_runtime_provenance_rejects_provider_switch() -> None:
    result = SimpleNamespace(
        meta=SimpleNamespace(
            provenance=SimpleNamespace(
                provider="eastmoney",
                channel="kline",
                capability="bars",
            )
        )
    )

    with pytest.raises(RuntimeProvenanceMismatchError):
        validate_runtime_provenance(
            RuntimeExecutionIdentity(
                provider="tdx",
                channel="quotation",
                capability="bars",
            ),
            result,
        )
