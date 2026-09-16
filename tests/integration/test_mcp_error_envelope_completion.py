from __future__ import annotations

import json

from tstdx.integration.mcp._common import ERR_METHOD_NOT_FOUND
from tstdx.integration.mcp_server import MCPServer


def test_mcp_method_not_found_preserves_rpc_code_and_adds_error_envelope() -> None:
    response = MCPServer().handle_request(
        {"jsonrpc": "2.0", "id": "missing-1", "method": "does/not/exist"}
    )
    assert response is not None
    assert response["error"]["code"] == ERR_METHOD_NOT_FOUND
    envelope = response["error"]["data"]
    assert envelope["code"] == "E1010"
    assert envelope["phase"] == "mcp_protocol"
    assert envelope["request_id"] == "missing-1"
    assert envelope["context"]["method"] == "does/not/exist"
    assert envelope["fallback_allowed"] is False
    assert envelope["provider_switch_allowed"] is False


def test_mcp_native_error_collapses_to_e9000_without_native_detail() -> None:
    class FailingServer(MCPServer):
        def _handle_tools_call(self, params):  # noqa: ANN001,ANN201
            raise RuntimeError("private-native-mcp-detail")

    response = FailingServer().handle_request(
        {"jsonrpc": "2.0", "id": "native-1", "method": "tools/call", "params": {}}
    )
    assert response is not None
    envelope = response["error"]["data"]
    encoded = json.dumps(response, ensure_ascii=False)
    assert envelope["code"] == "E9000"
    assert envelope["message"] == "internal error"
    assert envelope["phase"] == "mcp"
    assert envelope["request_id"] == "native-1"
    assert envelope["context"] == {}
    assert "private-native-mcp-detail" not in encoded
