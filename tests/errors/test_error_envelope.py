from __future__ import annotations

import pytest

from tstdx.domain.models import Quote
from tstdx.error_envelope import to_error_envelope
from tstdx.errors import SourceUnavailable, ValidationError
from tstdx.planned_service import UnifiedMarketDataService


def test_tdx_error_envelope_preserves_contract_and_redacts_sensitive_context() -> None:
    exc = ValidationError(
        "bad request",
        context={
            "provider": "tdx",
            "channel": "quotation",
            "capability": "quotes",
            "query_id": "q1:abc",
            "token": "secret-token",
            "cookie": "session=secret",
            "payload": "raw-binary",
            "safe_detail": "visible",
        },
    )
    envelope = to_error_envelope(exc, request_id="req-1")
    data = envelope.to_dict()

    assert data["code"] == "E1010"
    assert data["type"] == "ValidationError"
    assert data["message"] == "bad request"
    assert data["provider"] == "tdx"
    assert data["channel"] == "quotation"
    assert data["capability"] == "quotes"
    assert data["query_id"] == "q1:abc"
    assert data["request_id"] == "req-1"
    assert data["fallback_allowed"] is False
    assert data["provider_switch_allowed"] is False
    assert data["context"]["safe_detail"] == "visible"
    assert "token" not in data["context"]
    assert "cookie" not in data["context"]
    assert "payload" not in data["context"]


def test_native_exception_is_generic_public_internal_error() -> None:
    envelope = to_error_envelope(RuntimeError("database password=secret"))
    data = envelope.to_dict()
    assert data["code"] == "E9000"
    assert data["type"] == "InternalError"
    assert data["message"] == "internal error"
    assert data["context"] == {}
    assert data["fallback_allowed"] is False
    assert data["provider_switch_allowed"] is False


def test_planned_service_annotates_execution_error_with_query_identity() -> None:
    class Adapter:
        def fetch(self, symbols: list[str]) -> list[Quote]:
            raise SourceUnavailable("provider down")

    class Manager:
        def quote_adapter(self, provider: str):  # noqa: ANN202
            assert provider == "tencent"
            return Adapter()

        def close(self) -> None:
            pass

    service = UnifiedMarketDataService(manager=Manager())
    try:
        with pytest.raises(SourceUnavailable) as caught:
            service.quotes(["sh600519"], provider="tencent")
        context = caught.value.context
        assert context["provider"] == "tencent"
        assert context["channel"] == "quote"
        assert context["capability"] == "quotes"
        assert context["phase"] == "execution"
        assert context["fallback"] is False
        assert str(context["query_id"]).startswith("q1:")
    finally:
        service.close()
