from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import tstdx.cli as cli
from tstdx.errors import SourceUnavailable
from tstdx.integration.mcp_server import MCPServer
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
                raise SourceUnavailable(
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
    _assert_fail_closed_envelope(envelope, code="E7050")
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
            raise SourceUnavailable(
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
    _assert_fail_closed_envelope(envelope, code="E7050")
    assert envelope["request_id"] == "m1"
    assert envelope["provider"] == "sina"


def test_ws_domain_error_embeds_same_envelope() -> None:
    class FailingHandler(JsonRpcHandler):
        def _dispatch(self, method: str, params: Any) -> Any:
            raise SourceUnavailable(
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
    _assert_fail_closed_envelope(envelope, code="E7050")
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


def test_integration_package_exports_canonical_ws_handler() -> None:
    from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler as CanonicalHandler

    assert JsonRpcHandler is CanonicalHandler
