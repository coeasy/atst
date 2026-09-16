from __future__ import annotations

import json
import time

from tstdx.errors import ValidationError
from tstdx.integration.mcp._server import MCPServer
from tstdx.integration.runtime_tasks import RuntimeTaskStore
from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler


class _FailingPlanner:
    default_provider = "tdx"


class _FailingRuntimeKernel:
    planner = _FailingPlanner()


class _FailingClient:
    """Client-shaped double.

    ``RuntimeJsonRpcHandler`` (post v13 tier-a refactor) binds a ``Client``, not
    a raw runtime, so the failure surface lives on the client methods while the
    health/capabilities probes read ``client.runtime.planner``.
    """

    runtime = _FailingRuntimeKernel()

    def quotes(self, *args, **kwargs):
        raise RuntimeError("native secret")

    def bars(self, *args, **kwargs):
        raise ValidationError("bad bars", context={"provider": "tdx", "secret": "x"})

    def capabilities(self):
        return ()


def test_runtime_ws_native_error_is_safe_envelope() -> None:
    handler = RuntimeJsonRpcHandler(client=_FailingClient())
    response = json.loads(
        handler.handle_message(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "quotes",
                    "params": {"symbols": ["sh600519"]},
                }
            )
        )
    )
    assert response["error"]["code"] == -32603
    assert response["error"]["data"]["code"] == "E9000"
    assert "native secret" not in json.dumps(response)


def test_runtime_ws_notification_never_replies_on_failure() -> None:
    handler = RuntimeJsonRpcHandler(client=_FailingClient())
    assert (
        handler.handle_message(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "quotes",
                    "params": {"symbols": ["sh600519"]},
                }
            )
        )
        is None
    )


def test_mcp_domain_error_uses_canonical_envelope() -> None:
    class BadClient:
        pass

    server = MCPServer(client=BadClient())
    server._handle_tools_call = lambda params: (_ for _ in ()).throw(
        ValidationError("bad", context={"provider": "tdx", "secret": "x"})
    )
    response = server.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {}}
    )
    assert response is not None
    assert response["error"]["data"]["code"] == "E1010"
    assert response["error"]["data"]["context"] == {"provider": "tdx"}


def test_runtime_task_store_keeps_safe_envelope_and_expires_payload() -> None:
    store = RuntimeTaskStore(max_tasks=2, retention_seconds=0.01)

    def fail():
        raise RuntimeError("secret filesystem path")

    task_id = store.submit(fail)
    for _ in range(100):
        record = store.get(task_id)
        if record and record["status"] == "failed":
            break
        time.sleep(0.002)
    record = store.get(task_id)
    assert record is not None
    assert record["error"]["code"] == "E9000"
    assert "secret filesystem path" not in json.dumps(record)

    time.sleep(0.02)
    expired = store.get(task_id)
    assert expired is not None
    assert expired["expired"] is True
    assert expired["result"] is None
    assert expired["error"] is None
