"""MCP server unit tests (canonical v13 surface).

Smoke tests exercise the pure :meth:`MCPServer.handle_request` path —
no subprocess, no stdio, no network.  They verify:

* ``initialize`` returns serverInfo + capabilities
* ``tools/list`` returns the canonical 9 tools with name/description/inputSchema
* unknown method → JSON-RPC ``-32601``
* notifications (no id) return ``None``
* ``ping`` returns an empty result
* malformed / non-object requests produce a JSON-RPC error
* ``tools/call`` with an unknown tool name returns an ``isError`` result
* ``tools/call`` on a real tool (with a stubbed Client) returns text content
* every migrated business capability is reachable through ``query_capability``

The v15 clean break removed the v12 facade/dual-target tool surface
(``get_f10_catalog`` / ``get_adjusted_bars`` / ``get_all_market`` /
``get_minute_klines`` / ``get_board_quotes`` / ``get_board_members`` /
``get_ex_bars`` / ``get_goods_bars`` / ``get_index_list`` / ``search_symbols``
/ ``get_finance_info`` / ``get_capital_changes`` / ``list_servers`` /
``server_speedtest`` / ``get_stock_changes`` / ``get_hot_rank``). Those abilities
are now served by ``query_capability`` through the same Client /
QuerySpec / UnifiedRuntime chain as Python, CLI, HTTP and WS — the tests below
assert that routing instead of the retired dedicated tools.

These tests import ``MCPServer`` directly and pass a stub Client, so they never
touch the network.
"""

from __future__ import annotations

import json
import typing

import pytest

from tstdx.error_envelope import to_error_envelope
from tstdx.errors import ValidationError
from tstdx.integration.mcp._common import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
)
from tstdx.integration.mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
    MCPServer,
    create_mcp_server,
)
from tstdx.result import Provenance, ProvenanceKind, QueryResult, ResultMeta

#: The canonical v13 tool surface. ``query_capability`` is the gateway for every
#: migrated business capability; the remaining names are Tier-A ergonomic tools.
CANONICAL_TOOLS = {
    "query_capability",
    "get_bars",
    "get_quote",
    "get_quotes",
    "get_snapshot",
    "get_minute_today",
    "get_trades",
    "get_security_count",
    "get_security_list",
}


def _result(
    data: typing.Any,
    *,
    capability: str = "capability",
    provider: str = "tdx",
    channel: str = "quotation",
) -> QueryResult[typing.Any]:
    """Build a real ``QueryResult`` so transport serialization runs unchanged."""

    provenance = Provenance(
        provider=provider,
        channel=channel,
        capability=capability,
        kind=ProvenanceKind.DIRECT,
        observed_at_ns=1,
        provider_timestamp=None,
        cache_tier=None,
        requested_provider=None,
        fallback=False,
    )
    return QueryResult(
        data=data,
        meta=ResultMeta(
            provider=provider,
            channel=channel,
            capability=capability,
            fingerprint="q1:stub",
            provenance=provenance,
        ),
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def server() -> MCPServer:
    """An MCPServer with the default (never exercised) Client."""
    return MCPServer()


@pytest.fixture
def stub_client() -> object:
    """A fake canonical ``Client`` whose methods return deterministic data."""

    class Stub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, typing.Any]] = []

        def call(
            self,
            capability: str,
            *args: typing.Any,
            provider: str | None = None,
            channel: str | None = None,
            currentness: str = "business",
            max_age: float | None = None,
            use_cache: bool = True,
            **kwargs: typing.Any,
        ) -> QueryResult[typing.Any]:
            self.calls.append(("call", capability))
            return _result(
                {"capability": capability, "args": list(args), "kwargs": kwargs},
                capability=capability,
                provider=provider or "derived",
                channel=channel or "catalog",
            )

        def bars(
            self,
            symbol: str,
            *,
            provider: str | None = None,
            period: str = "day",
            count: int = 320,
            start: int = 0,
            adjustment: str = "",
            **kw: typing.Any,
        ) -> QueryResult[typing.Any]:
            self.calls.append(("bars", symbol))
            return _result(
                [{"symbol": symbol, "period": period, "count": count}],
                capability="bars",
            )

        def quotes(self, symbols: typing.Any, *, provider: str | None = None, **kw: typing.Any):
            self.calls.append(("quotes", symbols))
            if isinstance(symbols, str):
                symbols = [symbols]
            return _result(
                [{"code": item, "price": 1.0} for item in symbols],
                capability="quotes",
                channel="quote",
            )

        def snapshot(self, symbol: str, *, provider: str | None = None, **kw: typing.Any):
            self.calls.append(("snapshot", symbol))
            return _result({"symbol": symbol, "bids": [[1.0, 100]]})

        def minute(self, symbol: str, *, provider: str | None = None, **kw: typing.Any):
            self.calls.append(("minute", symbol))
            return _result([{"symbol": symbol, "minute": 0}])

        def trades(
            self,
            symbol: str,
            *,
            provider: str | None = None,
            start: int = 0,
            count: int = 0,
            **kw: typing.Any,
        ):
            self.calls.append(("trades", symbol))
            return _result([{"symbol": symbol, "price": 1.0}])

        def security_count(
            self,
            *,
            market: int | str = 0,
            provider: str | None = None,
            **kw: typing.Any,
        ):
            self.calls.append(("security_count", market))
            return _result({"count": 42})

        def security_list(
            self,
            *,
            market: int | str = 0,
            start: int = 0,
            provider: str | None = None,
            **kw: typing.Any,
        ):
            self.calls.append(("security_list", market))
            return _result([{"market": market, "start": start, "code": "600000"}])

    return Stub()


# --------------------------------------------------------------------------- #
# initialize
# --------------------------------------------------------------------------- #
def test_initialize_returns_server_info(server: MCPServer) -> None:
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"clientInfo": {"name": "test"}, "protocolVersion": PROTOCOL_VERSION},
    }
    resp = server.handle_request(req)
    assert resp is not None
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == SERVER_NAME
    assert result["serverInfo"]["version"] == SERVER_VERSION
    assert "capabilities" in result
    assert "tools" in result["capabilities"]


def test_initialize_without_params(server: MCPServer) -> None:
    req = {"jsonrpc": "2.0", "id": 2, "method": "initialize"}
    resp = server.handle_request(req)
    assert resp["result"]["serverInfo"]["name"] == SERVER_NAME


# --------------------------------------------------------------------------- #
# tools/list
# --------------------------------------------------------------------------- #
def test_tools_list_returns_all_tools(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert len(tools) == len(CANONICAL_TOOLS)
    assert {t["name"] for t in tools} == CANONICAL_TOOLS
    # Every tool must have the required MCP fields.
    for tool in tools:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        assert isinstance(tool["inputSchema"], dict)
        assert tool["inputSchema"]["type"] == "object"


def test_tools_list_matches_global_TOOLS(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == [t.name for t in TOOLS]
    assert set(names) == CANONICAL_TOOLS


def test_tools_list_exposes_no_retired_v12_tools(server: MCPServer) -> None:
    """The v15 clean break keeps only promoted capabilities in the manifest."""

    resp = server.handle_request({"jsonrpc": "2.0", "id": 40, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    retired = {
        "get_f10_catalog",
        "get_adjusted_bars",
        "get_all_market",
        "get_minute_klines",
        "get_board_quotes",
        "get_board_members",
        "get_ex_bars",
        "get_goods_bars",
        "get_index_list",
        "search_symbols",
        "get_finance_info",
        "get_capital_changes",
        "list_servers",
        "server_speedtest",
        "get_stock_changes",
        "get_hot_rank",
    }
    assert names & retired == set()


def test_tool_input_schemas_are_json_schema(server: MCPServer) -> None:
    """Each inputSchema must be a valid JSON Schema object with a type."""
    resp = server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    for tool in resp["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties", {}), dict)


# --------------------------------------------------------------------------- #
# unknown method
# --------------------------------------------------------------------------- #
def test_unknown_method_returns_32601(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 6, "method": "no/such/method"})
    assert "error" in resp
    assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND == -32601
    assert "no/such/method" in resp["error"]["message"]


def test_missing_method_returns_32601(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 7})
    assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND


# --------------------------------------------------------------------------- #
# notifications
# --------------------------------------------------------------------------- #
def test_notification_returns_none(server: MCPServer) -> None:
    """Notifications (no id) MUST NOT get a response."""
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert server.handle_request(req) is None


def test_initialized_notification_returns_none(server: MCPServer) -> None:
    req = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    assert server.handle_request(req) is None


def test_other_notification_returns_none(server: MCPServer) -> None:
    req = {"jsonrpc": "2.0", "method": "resources/list"}
    assert server.handle_request(req) is None


# --------------------------------------------------------------------------- #
# ping
# --------------------------------------------------------------------------- #
def test_ping_returns_empty_result(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 8, "method": "ping"})
    assert resp["result"] == {}


# --------------------------------------------------------------------------- #
# malformed requests
# --------------------------------------------------------------------------- #
def test_non_object_request_returns_error(server: MCPServer) -> None:
    resp = server.handle_request("not an object")  # type: ignore[arg-type]
    assert "error" in resp
    assert resp["error"]["code"] == -32600


def test_missing_jsonrpc_returns_method_not_found(server: MCPServer) -> None:
    # id present, method missing → treated as unknown method
    resp = server.handle_request({"jsonrpc": "2.0", "id": 9})
    assert resp["error"]["code"] == ERR_METHOD_NOT_FOUND


# --------------------------------------------------------------------------- #
# tools/call — unknown tool
# --------------------------------------------------------------------------- #
def test_tools_call_unknown_tool(server: MCPServer) -> None:
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "no/such/tool", "arguments": {}},
        }
    )
    assert "result" in resp
    content = resp["result"]["content"]
    assert content[0]["type"] == "text"
    assert "Unknown tool" in content[0]["text"]
    assert resp["result"]["isError"] is True


def test_tools_call_missing_arguments_defaults_to_empty(stub_client: object) -> None:
    """Missing ``arguments`` defaults to {} (tools without required args work)."""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "get_security_count"},
        }
    )
    assert "result" in resp
    assert resp["result"]["isError"] is False
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"]["count"] == 42


# --------------------------------------------------------------------------- #
# tools/call — Tier-A tools with a stub Client
# --------------------------------------------------------------------------- #
def test_tools_call_get_bars_with_stub(stub_client: object) -> None:
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "get_bars",
                "arguments": {"symbol": "sh600519", "period": "day", "count": 5},
            },
        }
    )
    assert "result" in resp
    assert resp["result"]["isError"] is False
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"][0]["symbol"] == "sh600519"
    assert parsed["data"][0]["count"] == 5
    assert parsed["meta"]["capability"] == "bars"


def test_tools_call_get_quote_with_stub(stub_client: object) -> None:
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "get_quote", "arguments": {"symbol": "sh600519"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"][0]["code"] == "sh600519"


def test_tools_call_get_quotes_with_stub(stub_client: object) -> None:
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {
                "name": "get_quotes",
                "arguments": {"symbols": ["sh600519", "sz000001"]},
            },
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert {q["code"] for q in parsed["data"]} == {"sh600519", "sz000001"}


def test_tools_call_get_security_count_with_stub(stub_client: object) -> None:
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {"name": "get_security_count", "arguments": {"market": "sh"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"]["count"] == 42


def test_tools_call_get_security_list_with_stub(stub_client: object) -> None:
    """证券代码表走 canonical Client（0x044D）。"""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 39,
            "method": "tools/call",
            "params": {"name": "get_security_list", "arguments": {"market": "sh"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"][0]["code"] == "600000"


def test_tools_call_get_snapshot_and_minute_and_trades(stub_client: object) -> None:
    """Tier-A 快照 / 分时 / 成交三个工具共用 Client 直连通道。"""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    for tool, fields in (
        ("get_snapshot", {"bids"}),
        ("get_minute_today", {"minute"}),
        ("get_trades", {"price"}),
    ):
        resp = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {"symbol": "sh600519"}},
            }
        )
        assert resp["result"]["isError"] is False
        parsed = json.loads(resp["result"]["content"][0]["text"])
        payload = parsed["data"][0] if isinstance(parsed["data"], list) else parsed["data"]
        assert payload["symbol"] == "sh600519"
        assert set(payload) >= fields


# --------------------------------------------------------------------------- #
# tools/call — query_capability gateway for migrated business abilities
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("capability", "args", "kwargs"),
    [
        ("f10_catalog", ["sh600519"], {}),
        ("adjusted_bars", ["sh600519"], {"method": "qfq"}),
        ("all_market", [], {"source": "tencent"}),
        ("minute_klines", ["sh600519"], {"period": "5min"}),
        ("board_members", ["new_blhy"], {}),
        ("ex_bars", ["hk00700"], {}),
        ("goods_bars", ["au2506"], {}),
        ("index_list", [], {}),
        ("search_symbols", ["茅台"], {"market": "sh"}),
        ("block_quotes", [], {"block_type": 0}),
        ("hot_rank", [], {"page": 1}),
        ("stock_changes", [], {"page": 1}),
    ],
)
def test_query_capability_routes_migrated_capabilities(
    stub_client: object,
    capability: str,
    args: list[typing.Any],
    kwargs: dict[str, typing.Any],
) -> None:
    """每个迁移能力都经 query_capability 走同一条 Client 执行链。"""

    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {"capability": capability, "args": args, "kwargs": kwargs},
            },
        }
    )
    assert resp["result"]["isError"] is False
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["data"]["capability"] == capability
    assert parsed["data"]["args"] == args
    assert parsed["meta"]["capability"] == capability


def test_query_capability_forwards_provider_channel_and_cache_hints(
    stub_client: object,
) -> None:
    """provider / channel / currentness / max_age / use_cache 原样透传。"""

    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {
                    "capability": "rank",
                    "provider": "eastmoney",
                    "channel": "rank",
                    "currentness": "live",
                    "max_age": 30,
                    "use_cache": False,
                },
            },
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed["meta"]["provider"] == "eastmoney"
    assert parsed["meta"]["channel"] == "rank"


def test_query_capability_requires_capability(server: MCPServer) -> None:
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {"name": "query_capability", "arguments": {}},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == ERR_INVALID_PARAMS


def test_query_capability_rejects_non_array_args(server: MCPServer) -> None:
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {"capability": "rank", "args": "not-an-array"},
            },
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == ERR_INVALID_PARAMS


def test_query_capability_rejects_non_object_kwargs(server: MCPServer) -> None:
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {"capability": "rank", "kwargs": ["not", "an", "object"]},
            },
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == ERR_INVALID_PARAMS


def test_query_capability_propagates_tdx_error_as_internal_error() -> None:
    """能力执行失败 → TdxError → JSON-RPC error（不回退默认 provider）。"""

    class FailingClient:
        def call(self, capability: str, *args: typing.Any, **kwargs: typing.Any):
            raise ValidationError("bogus payload", context={"capability": capability})

    server = MCPServer(client=FailingClient())  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 35,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {"capability": "adjusted_bars", "args": ["sh600519"]},
            },
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] != ERR_METHOD_NOT_FOUND
    assert resp["error"]["data"]["type"] == "ValidationError"


def test_tools_call_with_non_object_arguments(server: MCPServer) -> None:
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {"name": "get_bars", "arguments": "not-an-object"},
        }
    )
    assert "result" in resp
    assert resp["result"]["isError"] is True
    assert "arguments must be an object" in resp["result"]["content"][0]["text"]


def test_tools_call_missing_required_arg_returns_error_response(
    stub_client: object,
) -> None:
    """Missing a required argument → KeyError → -32602 invalid params."""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {"name": "get_bars", "arguments": {}},
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == ERR_INVALID_PARAMS
    data = resp["error"]["data"]
    assert data["phase"] == "mcp"
    assert data["type"] == "ValidationError"


# --------------------------------------------------------------------------- #
# client ownership / laziness
# --------------------------------------------------------------------------- #
def test_metadata_methods_never_touch_the_client() -> None:
    """initialize / tools/list / ping 不触发任何 Client 调用（保持惰性）。"""

    class Boom:
        def __getattr__(self, name: str):
            raise AssertionError(f"client must not be used for metadata: {name}")

    server = MCPServer(client=Boom())  # type: ignore[arg-type]
    for method in ("initialize", "tools/list", "ping"):
        resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": method})
        assert "result" in resp


def test_default_server_owns_its_client() -> None:
    server = MCPServer()
    assert server._owns_client is True
    server.stop()  # closing an owned client must be safe with no prior I/O


def test_injected_client_is_not_owned_and_not_closed() -> None:
    class Counting:
        closed = False

        def close(self) -> None:
            type(self).closed = True

    injected = Counting()
    server = MCPServer(client=injected)  # type: ignore[arg-type]
    assert server._owns_client is False
    server.stop()
    assert Counting.closed is False


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def test_create_mcp_server_factory() -> None:
    server = create_mcp_server()
    assert isinstance(server, MCPServer)


def test_create_mcp_server_with_client() -> None:
    sentinel = object()
    server = create_mcp_server(client=sentinel)  # type: ignore[arg-type]
    assert server._client is sentinel


# --------------------------------------------------------------------------- #
# serve() integration (minimal, no real stdio)
# --------------------------------------------------------------------------- #
def test_serve_loop_reads_and_responds(monkeypatch) -> None:
    """End-to-end: feed lines to serve() via a fake stdin, capture stdout."""
    import io

    server = MCPServer()
    stdin_buf = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
        + "\n"  # blank line should be skipped
        + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        + "\n"
    )
    stdout_buf = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_buf)
    monkeypatch.setattr("sys.stdout", stdout_buf)

    server.serve()

    lines = [ln for ln in stdout_buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 3  # initialize, tools/list, ping (notification dropped)
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == SERVER_NAME
    assert len(json.loads(lines[1])["result"]["tools"]) == len(CANONICAL_TOOLS)
    assert json.loads(lines[2])["result"] == {}


def test_serve_loop_stops_on_stop_signal(monkeypatch) -> None:
    """stop() must break the serve() loop (even with blocking stdin)."""
    import io

    server = MCPServer()
    # Empty stdin → immediate EOF → serve returns naturally.
    stdin_buf = io.StringIO("")
    stdout_buf = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_buf)
    monkeypatch.setattr("sys.stdout", stdout_buf)
    server.serve()
    assert stdout_buf.getvalue() == ""


def test_serve_loop_handles_malformed_json(monkeypatch) -> None:
    """Malformed JSON → parse error response (id: null)."""
    import io

    server = MCPServer()
    stdin_buf = io.StringIO("this is not json\n")
    stdout_buf = io.StringIO()
    monkeypatch.setattr("sys.stdin", stdin_buf)
    monkeypatch.setattr("sys.stdout", stdout_buf)
    server.serve()
    lines = [ln for ln in stdout_buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["id"] is None
    assert parsed["error"]["code"] == -32700


def test_error_envelope_helper_matches_transport_contract() -> None:
    """传输层错误信封与 tstdx 统一 ErrorEnvelope 契约一致。"""

    envelope = to_error_envelope(ValidationError("boom", context={"phase": "mcp"}))
    assert envelope.type == "ValidationError"
    assert envelope.to_dict()["type"] == "ValidationError"
    assert envelope.phase == "mcp"
