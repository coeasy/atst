from __future__ import annotations

import io
import json
from typing import Any

from atst.integration.mcp import MCPServer


def test_malformed_stdio_json_uses_canonical_error_envelope(monkeypatch) -> None:  # noqa: ANN001
    stdin = io.StringIO("{bad-json\n")
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)

    MCPServer().serve()

    payload = json.loads(stdout.getvalue().strip())
    envelope = payload["error"]["data"]
    assert payload["error"]["code"] == -32700
    assert envelope["code"] == "E1010"
    assert envelope["phase"] == "mcp_protocol"
    assert envelope["fallback_allowed"] is False
    assert envelope["provider_switch_allowed"] is False


def test_injected_mcp_client_is_not_closed_by_server() -> None:
    closed: list[str] = []

    class Resource:
        def close(self) -> None:
            closed.append("closed")

    server = MCPServer(client=Resource())  # type: ignore[arg-type]
    server.stop()

    assert closed == []


def test_owned_mcp_client_is_closed_once_by_stop() -> None:
    closed: list[str] = []

    class Resource:
        def close(self) -> None:
            closed.append("closed")

    server = MCPServer()  # owns its default Client
    server._client = Resource()  # probe the owned handle
    server.stop()
    server.stop()

    assert closed == ["closed"]


def test_mcp_notification_still_has_no_response() -> None:
    server = MCPServer()
    response = server.handle_request(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    assert response is None


def test_mcp_shutdown_response_is_preserved() -> None:
    server = MCPServer()
    response: dict[str, Any] | None = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "shutdown"}
    )
    assert response == {"jsonrpc": "2.0", "id": 1, "result": {}}
