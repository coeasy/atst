from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import tstdx.cli as cli
from tstdx.errors import WebSourceError
from tstdx.integration.mcp import MCPServer
from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler as JsonRpcHandler


def _assert_fail_closed_envelope(data: dict[str, Any], *, code: str) -> None:
    assert data["code"] == code
    assert data["fallback_allowed"] is False
    assert data["provider_switch_allowed"] is False
    if data["code"] != "E9000":
        assert data["context"]["fallback"] is False
        assert data["context"]["fallback_allowed"] is False
        assert data["context"]["provider_switch_allowed"] is False
    assert "traceback" not in json.dumps(data, ensure_ascii=False).lower()


def test_cli_tdx_error_emits_canonical_envelope(monkeypatch, capsys) -> None:  # noqa: ANN001
    class Parser:
        def parse_args(self, argv):  # noqa: ANN001,ANN201
            def fail(args):  # noqa: ANN001,ANN202
                raise WebSourceError(
                    "selected provider unavailable",
                    context={
                        "provider": "tdx",
                        "channel": "quotation",
                        "capability": "quotes",
                        "authorization": "must-not-leak",
                        "fallback": True,
                    },
                )

            return SimpleNamespace(func=fail)

    monkeypatch.setattr(cli, "build_parser", lambda: Parser())
    assert cli.main([]) == 2
    payload = json.loads(capsys.readouterr().err)
    envelope = payload["error"]
    _assert_fail_closed_envelope(envelope, code="E7000")
    assert envelope["provider"] == "tdx"
    assert "authorization" not in envelope["context"]


def test_cli_native_error_is_e9000_without_native_message(monkeypatch, capsys) -> None:  # noqa: ANN001
    class Parser:
        def parse_args(self, argv):  # noqa: ANN001,ANN201
            def fail(args):  # noqa: ANN001,ANN202
                raise RuntimeError("secret-native-detail")

            return SimpleNamespace(func=fail)

    monkeypatch.setattr(cli, "build_parser", lambda: Parser())
    assert cli.main([]) == 1
    payload = json.loads(capsys.readouterr().err)
    envelope = payload["error"]
    _assert_fail_closed_envelope(envelope, code="E9000")
    assert envelope["message"] == "internal error"
    assert envelope["context"] == {}
    assert "secret-native-detail" not in json.dumps(payload)


def test_mcp_domain_error_embeds_same_envelope() -> None:
    class FailingServer(MCPServer):
        def _handle_tools_call(self, params):  # noqa: ANN001,ANN201
            raise WebSourceError(
                "provider unavailable",
                context={
                    "provider": "sina",
                    "channel": "quote",
                    "capability": "quotes",
                    "fallback": True,
                },
            )

    response = FailingServer().handle_request(
        {"jsonrpc": "2.0", "id": "m1", "method": "tools/call", "params": {}}
    )
    assert response is not None
    envelope = response["error"]["data"]
    _assert_fail_closed_envelope(envelope, code="E7000")
    assert envelope["request_id"] == "m1"
    assert envelope["provider"] == "sina"


def test_ws_domain_error_embeds_same_envelope() -> None:
    class FailingHandler(JsonRpcHandler):
        def _dispatch(self, method: str, params: Any) -> Any:
            raise WebSourceError(
                "provider unavailable",
                context={
                    "provider": "tencent",
                    "channel": "quote",
                    "capability": "quotes",
                    "fallback": True,
                },
            )

    raw = FailingHandler().handle_message(
        json.dumps({"jsonrpc": "2.0", "id": "w1", "method": "quotes", "params": {}})
    )
    assert raw is not None
    response = json.loads(raw)
    envelope = response["error"]["data"]
    _assert_fail_closed_envelope(envelope, code="E7000")
    assert envelope["request_id"] == "w1"
    assert envelope["provider"] == "tencent"


def test_ws_native_error_is_e9000_and_does_not_leak_details() -> None:
    class FailingHandler(JsonRpcHandler):
        def _dispatch(self, method: str, params: Any) -> Any:
            raise RuntimeError("private-upstream-detail")

    raw = FailingHandler().handle_message(
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "quotes", "params": {}})
    )
    assert raw is not None
    response = json.loads(raw)
    envelope = response["error"]["data"]
    _assert_fail_closed_envelope(envelope, code="E9000")
    assert envelope["message"] == "internal error"
    assert envelope["context"] == {}
    assert "private-upstream-detail" not in raw


def test_ws_protocol_failures_carry_the_same_envelope_as_mcp() -> None:
    """WS 面三条协议层失败路径都要挂信封——第 30 轮修前一条都没有。

    与 MCP 面的 ``test_mcp_method_not_found_preserves_rpc_code_and_adds_error_envelope``
    配对：两面同一类失败的 ``error.data`` 必须逐格同形。``docs/errors.md`` §四 与
    ``RuntimeJsonRpcHandler._error`` 的 docstring 都写着"信封挂在 JSON-RPC ``error.data``"，
    而修前 WS 的解析失败 / 非法请求 / 未知方法只回 ``{"code", "message"}``——一个照文档
    读 ``data.phase`` 的客户端，恰好在"方法名写错"这种最该自查的一格上读到 ``None``。
    """

    cases = [
        ("parse error", "{bad-json", -32700, None),
        (
            "invalid request",
            json.dumps({"jsonrpc": "1.0", "id": 9, "method": "quotes"}),
            -32600,
            "9",
        ),
        (
            "method not found",
            json.dumps({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}),
            -32601,
            "9",
        ),
    ]
    for label, payload, rpc_code, expected_request_id in cases:
        raw = JsonRpcHandler().handle_message(payload)
        assert raw is not None
        error = json.loads(raw)["error"]
        assert error["code"] == rpc_code, label
        envelope = error["data"]
        _assert_fail_closed_envelope(envelope, code="E1010")
        assert envelope["phase"] == "ws_protocol", label
        if expected_request_id is None:
            # 解析失败时连 id 都还没读出来：不许凭空造一个 request_id。
            assert "request_id" not in envelope, label
        else:
            assert envelope["request_id"] == expected_request_id, label


def test_integration_package_exports_canonical_ws_handler() -> None:
    from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler as CanonicalHandler

    assert JsonRpcHandler is CanonicalHandler
