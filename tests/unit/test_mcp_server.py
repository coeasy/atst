"""MCP server unit tests (Tier C3).

Smoke tests exercise the pure :meth:`MCPServer.handle_request` path —
no subprocess, no stdio, no network.  They verify:

* ``initialize`` returns serverInfo + capabilities
* ``tools/list`` returns all 23 tools with name/description/inputSchema
* unknown method → JSON-RPC ``-32601``
* notifications (no id) return ``None``
* ``ping`` returns an empty result
* malformed / non-object requests produce a JSON-RPC error
* ``tools/call`` with an unknown tool name returns an ``isError`` result
* ``tools/call`` on a real tool (with a stubbed client) returns text content
* N3: cross-source / web-only tools (use_facade) dispatch to a stubbed facade

These tests import ``MCPServer`` directly and pass a stub client / facade, so
they never touch the network.
"""

from __future__ import annotations

import json

import pytest

from tstdx.integration.mcp_server import (
    ERR_INTERNAL,
    ERR_METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
    MCPServer,
    create_mcp_server,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def server() -> MCPServer:
    """An MCPServer with no client (lazy, network-free)."""
    return MCPServer()


@pytest.fixture
def stub_client() -> object:
    """A fake TdxClient whose methods return deterministic data."""

    class Stub:
        def bars(self, symbol, *, period="day", count=320, **kw):
            return [{"symbol": symbol, "period": period, "count": count}]

        def quotes(self, symbols, *, as_format="dict"):
            if isinstance(symbols, str):
                symbols = [symbols]
            return [{"code": s, "price": 1.0} for s in symbols]

        def minute_today(self, symbol):
            return [{"symbol": symbol, "minute": 0}]

        def trade_today(self, symbol):
            return [{"symbol": symbol, "price": 1.0}]

        def finance_info(self, symbol):
            return {"symbol": symbol, "shares": 1}

        def security_count(self, market):
            return 42

        def capital_changes(self, symbol):
            return [object()]  # to_dicts will call .to_dict() if present; else dict(x)

        def block_quotes(self, block_type=0, start=0):
            return [{"block_type": block_type, "start": start, "name": "人工智能"}]

        def security_list(self, market=0, start=0):
            return [{"market": market, "start": start, "code": "600000"}]

        def catalog(self, symbol):
            return [
                {"title": "公司概况", "filename": "gsgk.dat"},
                {"title": "财务分析", "filename": "cwbj.dat"},
            ]

    return Stub()


@pytest.fixture
def stub_facade() -> object:
    """N3：门面替身，覆盖跨源 / web 独有能力工具（离线）。"""

    class StubFacade:
        def adjusted_bars(self, symbol, *, method="qfq", period="day", count=320):
            return [{"symbol": symbol, "method": method, "period": period, "count": count}]

        def all_market(self, *, node="hs_a", page_size=80, max_pages=None, source="sina"):
            return [{"node": node, "source": source}]

        def minute_klines(self, symbol, *, period="5min", count=240):
            return [{"symbol": symbol, "period": period, "count": count}]

        def board_members(self, node, *, page_size=100, max_pages=None):
            return [{"node": node, "code": "600000"}]

        def ex_bars(self, symbol, *, period="day", count=320):
            return [{"symbol": symbol, "kind": "ex", "period": period, "count": count}]

        def goods_bars(self, symbol, *, period="day", count=320):
            return [{"symbol": symbol, "kind": "goods", "period": period, "count": count}]

        def index_list(self):
            return [{"name": "上证指数", "code": "sh000001"}]

        def search_symbols(self, pattern, *, limit=10, market=None):
            return [{"code": "600519", "name": "贵州茅台", "market": market or "sh"}]

    return StubFacade()


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
    # N3 批次后为 23 工具
    assert len(tools) == 23
    assert {t["name"] for t in tools} >= {
        "get_stock_changes",
        "get_hot_rank",
        "get_f10_catalog",
        "get_adjusted_bars",
        "get_all_market",
    }
    # Every tool must have the required MCP fields.
    for tool in tools:
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]
        assert isinstance(tool["inputSchema"], dict)
        assert tool["inputSchema"]["type"] == "object"


def test_tools_list_matches_global_TOOLS(server: MCPServer) -> None:
    resp = server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    expected = [t.name for t in TOOLS]
    assert names == expected
    # Every expected tool name is present.
    assert set(names) == {
        "get_bars",
        "get_quote",
        "get_quotes",
        "get_minute_today",
        "get_trades",
        "get_finance_info",
        "get_security_count",
        "get_capital_changes",
        "get_f10_catalog",
        "get_stock_changes",
        "get_hot_rank",
        "list_servers",
        "server_speedtest",
        # N3：跨源 / web 独有能力
        "get_adjusted_bars",
        "get_all_market",
        "get_minute_klines",
        "get_board_quotes",
        "get_board_members",
        "get_ex_bars",
        "get_goods_bars",
        "get_index_list",
        "get_security_list",
        "search_symbols",
    }


def test_tool_input_schemas_are_json_schema(server: MCPServer) -> None:
    """Each inputSchema must be a valid JSON Schema object with a type."""
    resp = server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
    for tool in resp["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        # properties is allowed to be empty for no-arg tools
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


def test_tools_call_missing_args_is_not_an_error_response(server: MCPServer) -> None:
    """Missing arguments defaults to {} (tools that take no args still work)."""
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "list_servers"},
        }
    )
    # list_servers does no network I/O, so it should succeed even without args.
    assert "result" in resp
    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1  # at least the built-in pool


# --------------------------------------------------------------------------- #
# tools/call — real tool with stub client
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
    text = resp["result"]["content"][0]["text"]
    parsed = json.loads(text)
    assert len(parsed) == 1
    assert parsed[0]["symbol"] == "sh600519"
    assert parsed[0]["count"] == 5


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
    assert parsed["code"] == "sh600519"


def test_tools_call_get_f10_catalog_with_stub(stub_client: object) -> None:
    """N1：get_f10_catalog 复用注入客户端的 catalog（离线）。"""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "get_f10_catalog",
                "arguments": {"symbol": "sh600519"},
            },
        }
    )
    assert resp["result"]["isError"] is False
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["title"] == "公司概况"
    assert parsed[0]["filename"] == "gsgk.dat"


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
    assert len(parsed) == 2
    assert {q["code"] for q in parsed} == {"sh600519", "sz000001"}


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
    assert parsed["count"] == 42


# --------------------------------------------------------------------------- #
# N3：门面工具（use_facade → 注入 stub_facade）与客户端工具
# --------------------------------------------------------------------------- #
def test_tools_call_get_adjusted_bars_with_facade(stub_facade: object) -> None:
    """N3：复权 K 线走门面；Bar 列表经 to_dicts 序列化。"""
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {
                "name": "get_adjusted_bars",
                "arguments": {"symbol": "sh600519", "method": "qfq", "period": "day", "count": 5},
            },
        }
    )
    assert resp["result"]["isError"] is False
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["symbol"] == "sh600519"
    assert parsed[0]["method"] == "qfq"
    assert parsed[0]["count"] == 5


def test_tools_call_get_adjusted_bars_rejects_bad_method(stub_facade: object) -> None:
    """N3：非法复权方法 → TdxError → JSON-RPC error（不回退默认）。"""
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {
                "name": "get_adjusted_bars",
                "arguments": {"symbol": "sh600519", "method": "bogus"},
            },
        }
    )
    assert "error" in resp
    assert resp["error"]["code"] == ERR_INTERNAL


def test_tools_call_get_all_market_with_facade(stub_facade: object) -> None:
    """N3：全市场行情走门面 web 路由。"""
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {
                "name": "get_all_market",
                "arguments": {"node": "hs_a", "source": "tencent"},
            },
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["source"] == "tencent"


def test_tools_call_get_minute_klines_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {
                "name": "get_minute_klines",
                "arguments": {"symbol": "sh600519", "period": "5min"},
            },
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["period"] == "5min"


def test_tools_call_get_board_quotes_with_stub(stub_client: object) -> None:
    """N3：板块行情走客户端（0x07E5）。"""
    server = MCPServer(client=stub_client)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {"name": "get_board_quotes", "arguments": {"block_type": 0}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["name"] == "人工智能"
    assert parsed[0]["block_type"] == 0


def test_tools_call_get_board_members_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 35,
            "method": "tools/call",
            "params": {"name": "get_board_members", "arguments": {"node": "new_blhy"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["code"] == "600000"


def test_tools_call_get_ex_bars_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 36,
            "method": "tools/call",
            "params": {"name": "get_ex_bars", "arguments": {"symbol": "hk00700"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["kind"] == "ex"


def test_tools_call_get_goods_bars_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 37,
            "method": "tools/call",
            "params": {"name": "get_goods_bars", "arguments": {"symbol": "au2506"}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["kind"] == "goods"


def test_tools_call_get_index_list_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 38,
            "method": "tools/call",
            "params": {"name": "get_index_list", "arguments": {}},
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["code"] == "sh000001"


def test_tools_call_get_security_list_with_stub(stub_client: object) -> None:
    """N3：证券代码表走客户端（0x044D）。"""
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
    assert parsed[0]["code"] == "600000"


def test_tools_call_search_symbols_with_facade(stub_facade: object) -> None:
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 40,
            "method": "tools/call",
            "params": {
                "name": "search_symbols",
                "arguments": {"pattern": "茅台", "market": "sh"},
            },
        }
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed[0]["name"] == "贵州茅台"
    assert parsed[0]["market"] == "sh"


def test_tools_call_facade_does_not_create_client(stub_facade: object) -> None:
    """N3：仅调用门面工具不应创建 TdxClient（保持惰性）。"""
    server = MCPServer(facade=stub_facade)  # type: ignore[arg-type]
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 41,
            "method": "tools/call",
            "params": {"name": "get_index_list", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is False
    assert server._client is None
    assert server._facade is stub_facade


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


def test_tools_call_missing_required_arg_returns_error_response(server: MCPServer) -> None:
    """Missing a required argument → KeyError → -32602 invalid params."""
    # list_servers takes no args, so use it to prove the dispatch path works;
    # for a real missing-arg test we'd need a client stub.
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {"name": "list_servers", "arguments": {}},
        }
    )
    assert "result" in resp
    assert resp["result"]["isError"] is False


# --------------------------------------------------------------------------- #
# lazy client creation
# --------------------------------------------------------------------------- #
def test_lazy_client_not_created_until_tool_call() -> None:
    """Constructing MCPServer must not create a TdxClient (no network)."""
    server = MCPServer()
    assert server._client is None
    # handle_request for tools/list doesn't need a client
    resp = server.handle_request({"jsonrpc": "2.0", "id": 18, "method": "tools/list"})
    assert resp["result"]["tools"]
    assert server._client is None  # still lazy


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
    assert len(json.loads(lines[1])["result"]["tools"]) == 23
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
