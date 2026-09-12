from __future__ import annotations

import pytest

from tstdx.facade import RuntimeFacadeAdapter
from tstdx.provider import Provider
from tstdx.runtime import Runtime


class _EchoProvider(Provider):
    name = "tdx"

    def query(self, request):
        return {
            "operation": request.operation,
            "args": request.args,
            "params": request.params,
        }


class _CodedError(RuntimeError):
    code = "E1234"


class _ErrorProvider(Provider):
    name = "web"

    def query(self, request):
        raise _CodedError("provider failed")


def test_facade_adapter_preserves_call_shape_and_route() -> None:
    runtime = Runtime()
    runtime.register_provider(_EchoProvider())
    adapter = RuntimeFacadeAdapter(runtime)

    response = adapter.query("bars", "sh600519", count=10, route="tdx")

    assert response.success is True
    assert response.data == {
        "operation": "bars",
        "args": ("sh600519",),
        "params": {"count": 10},
    }
    assert response.extra["provider"] == "tdx"
    assert response.extra["operation"] == "bars"
    assert response.extra["cache_key"]


def test_facade_adapter_preserves_runtime_error_code() -> None:
    runtime = Runtime()
    runtime.register_provider(_ErrorProvider())
    adapter = RuntimeFacadeAdapter(runtime)

    response = adapter.query("quotes", ["sh600519"], route="web")

    assert response.success is False
    assert response.code == "E1234"
    assert response.error == "provider failed"
    assert response.extra["operation"] == "quotes"


def test_facade_adapter_auto_route_does_not_force_provider() -> None:
    runtime = Runtime()
    runtime.register_provider(_EchoProvider())
    adapter = RuntimeFacadeAdapter(runtime)

    request = adapter.build_request("quotes", ["sh600519"], route="auto")

    assert "provider" not in request.metadata


def test_facade_adapter_rejects_unknown_route() -> None:
    adapter = RuntimeFacadeAdapter(Runtime())

    with pytest.raises(ValueError, match="unsupported runtime route"):
        adapter.build_request("quotes", ["sh600519"], route="unknown")
