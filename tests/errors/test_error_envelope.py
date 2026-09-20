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


def test_exhausted_query_deadline_is_never_advertised_as_retryable() -> None:
    """预算耗尽是一条**决策**，不该被传输层的静态建议盖过去。

    ``ReadTimeout`` 的 advice 写着"读取超时：可重试"，而查询总 deadline 已经用完
    时重试同一 Provider 只会再撞一次同一个时钟。信封在这里取得显式
    ``retry_same_provider``，wire 上的 ``retryable`` 因此不再撒谎。
    """
    import time

    from tstdx.errors import ReadTimeout
    from tstdx.query import ExecutionBudget

    with pytest.raises(ReadTimeout) as caught:
        ExecutionBudget(deadline_ns=time.monotonic_ns() - 1).ensure_remaining("provider_request")

    assert caught.value.advice.retryable is True  # 静态建议没变
    envelope = to_error_envelope(caught.value).to_dict()
    assert envelope["retryable"] is False
    assert envelope["context"]["retry_same_provider"] is False
