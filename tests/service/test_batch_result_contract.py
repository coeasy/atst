from __future__ import annotations

import copy
from typing import Any, cast

import pytest

from tstdx.batch import BatchResult
from tstdx.error_envelope import ErrorEnvelope


def _error(*, status: str | None = None, context: dict[str, Any] | None = None) -> ErrorEnvelope:
    merged = dict(context or {})
    if status is not None:
        merged["batch_status"] = status
    return ErrorEnvelope(
        code="E7050",
        type="SourceUnavailable",
        message="missing",
        phase="batch",
        provider="tdx",
        channel="quotation",
        capability="quotes",
        partial=True,
        context=merged,
    )


def test_batch_result_requires_partial_to_match_errors() -> None:
    with pytest.raises(ValueError, match="partial"):
        BatchResult(items=(), errors={"sh600519": _error()}, partial=False)

    with pytest.raises(ValueError, match="partial"):
        BatchResult(items=(), errors={}, partial=True)


def test_batch_result_defensively_copies_and_freezes_error_mapping() -> None:
    source_context: dict[str, Any] = {"batch_status": "missing", "nested": {"round": 1}}
    source_error = _error(context=source_context)
    source = {"sh600519": source_error}
    result = BatchResult(items=(), errors=source, requested=("sh600519",), partial=True)

    source.clear()
    source_context["batch_status"] = "failed"
    source_context["nested"]["round"] = 2

    assert result.partial is True
    assert result.success is False
    assert set(result.errors) == {"sh600519"}
    assert result.status_for("sh600519") == "missing"
    assert result.errors["sh600519"].context["nested"]["round"] == 1
    assert result.to_dict()["partial"] is True

    with pytest.raises(TypeError):
        cast(Any, result.errors)["sz000001"] = _error()


def test_batch_result_deepcopy_preserves_immutable_independent_audit() -> None:
    original = BatchResult(
        items=("quote-a",),
        errors={
            "sz000001": _error(
                status="missing",
                context={"nested": {"attempt": 1}},
            )
        },
        requested=("sh600519", "sz000001"),
        partial=True,
        meta={"provider": "tdx", "nested": {"epoch": 1}},
    )

    cloned = copy.deepcopy(original)

    assert cloned is not original
    assert cloned.errors is not original.errors
    assert cloned.errors["sz000001"] is not original.errors["sz000001"]
    assert cloned.to_dict() == original.to_dict()
    cast(dict[str, Any], cloned.errors["sz000001"].context)["nested"]["attempt"] = 2
    cast(dict[str, Any], cloned.meta)["nested"]["epoch"] = 2
    assert original.errors["sz000001"].context["nested"]["attempt"] == 1
    assert original.meta["nested"]["epoch"] == 1


def test_batch_result_rejects_error_for_unrequested_symbol() -> None:
    with pytest.raises(ValueError, match="未请求"):
        BatchResult(
            items=(),
            errors={"sz000001": _error(status="missing")},
            requested=("sh600519",),
            partial=True,
        )


def test_batch_result_statuses_are_auditable_and_duplicate_requests_stay_compact() -> None:
    result = BatchResult(
        items=("quote-a",),
        errors={
            "sz000001": _error(status="missing"),
            "sh688001": _error(status="not_attempted"),
        },
        requested=("sh600519", "sz000001", "sh688001", "sh600519"),
        partial=True,
    )

    assert result.status_for("sh600519") == "ok"
    assert result.status_for("sz000001") == "missing"
    assert result.status_for("sh688001") == "not_attempted"
    assert dict(result.status_counts) == {
        "ok": 1,
        "failed": 0,
        "missing": 1,
        "not_attempted": 1,
    }
    with pytest.raises(KeyError):
        result.status_for("bj430047")
