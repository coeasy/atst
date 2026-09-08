from __future__ import annotations

import pytest

from tstdx.batch import BatchResult
from tstdx.error_envelope import ErrorEnvelope


def _error() -> ErrorEnvelope:
    return ErrorEnvelope(
        code="E7050",
        type="SourceUnavailable",
        message="missing",
        phase="batch",
        provider="tdx",
        channel="quotation",
        capability="quotes",
        partial=True,
    )


def test_batch_result_requires_partial_to_match_errors() -> None:
    with pytest.raises(ValueError, match="partial"):
        BatchResult(items=(), errors={"sh600519": _error()}, partial=False)

    with pytest.raises(ValueError, match="partial"):
        BatchResult(items=(), errors={}, partial=True)


def test_batch_result_defensively_copies_error_mapping() -> None:
    source = {"sh600519": _error()}
    result = BatchResult(items=(), errors=source, requested=("sh600519",), partial=True)

    source.clear()

    assert result.partial is True
    assert result.success is False
    assert set(result.errors) == {"sh600519"}
    assert result.to_dict()["partial"] is True
