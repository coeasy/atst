from __future__ import annotations

from typing import Any

import pytest

from tstdx.errors import InternalError, ValidationError
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.query import QueryPlan, QuerySpec


class RaisingService(UnifiedMarketDataService):
    def __init__(self, error: BaseException) -> None:
        super().__init__(cache_enabled=False)
        self.error = error

    def _execute_quotes_plan(self, plan: QueryPlan, *, with_meta: bool) -> Any:  # noqa: ARG002
        raise self.error


def _spec() -> QuerySpec:
    return QuerySpec.build(
        "quotes",
        symbols=("sh600519",),
        provider="tencent",
    )


def test_existing_tdx_error_is_preserved_by_planned_boundary() -> None:
    original = ValidationError("bad input")
    service = RaisingService(original)

    try:
        with pytest.raises(ValidationError) as caught:
            service.query(_spec())
    finally:
        service.close()

    assert caught.value is original
    assert caught.value.context["provider"] == "tencent"
    assert caught.value.context["channel"] == "quote"
    assert caught.value.context["capability"] == "quotes"
    assert caught.value.context["phase"] == "execution"
    assert caught.value.context["fallback"] is False


def test_native_exception_is_wrapped_as_internal_error() -> None:
    original = ValueError("unexpected provider shape")
    service = RaisingService(original)

    try:
        with pytest.raises(InternalError) as caught:
            service.query(_spec())
    finally:
        service.close()

    assert caught.value.code == "E9000"
    assert caught.value.context["provider"] == "tencent"
    assert caught.value.context["channel"] == "quote"
    assert caught.value.context["capability"] == "quotes"
    assert caught.value.context["phase"] == "execution"
    assert caught.value.context["fallback"] is False
    assert caught.value.context["cause_type"] == "ValueError"
    assert caught.value.cause is original
    assert caught.value.__cause__ is original


def test_base_exception_is_not_normalized_to_internal_error() -> None:
    original = KeyboardInterrupt()
    service = RaisingService(original)

    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            service.query(_spec())
    finally:
        service.close()

    assert caught.value is original
