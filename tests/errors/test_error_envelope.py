from __future__ import annotations

import pytest

from tstdx.error_envelope import to_error_envelope
from tstdx.errors import ValidationError, WebSourceError


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


def test_kernel_propagates_execution_error_without_fallback() -> None:
    """A failing Provider surfaces its own envelope identity; the kernel never retries."""
    from tstdx.runtime.kernel import UnifiedRuntime

    calls: list[str] = []

    class Executor:
        def execute(self, plan):  # noqa: ANN001, ANN201
            calls.append(str(plan.spec.provider))
            raise WebSourceError("provider down", context={"provider": plan.spec.provider})

    runtime = UnifiedRuntime()
    runtime.executor = Executor()
    try:
        with pytest.raises(WebSourceError) as caught:
            runtime.quotes(["sh600519"], provider="tencent")
        assert calls == ["tencent"]
        assert caught.value.context["provider"] == "tencent"
        assert caught.value.context.get("fallback") is None
    finally:
        runtime.close()
