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
