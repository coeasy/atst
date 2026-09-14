# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""L5 canonical transport smoke tests.

These tests exercise real loopback HTTP/WebSocket transports plus MCP stdio,
while keeping Provider I/O offline through a deterministic fake Client.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
import time
import urllib.request

import pytest

from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult

pytestmark = pytest.mark.integration


class SmokeRuntime:
    class Planner:
        default_provider = "tdx"

    class Executor:
        _bindings = {("tdx", "quotation", "quotes"): object()}

    planner = Planner()
    executor = Executor()


class SmokeClient:
    runtime = SmokeRuntime()

    @staticmethod
    def _result(capability: str, *, symbols=(), data=None):
        kwargs = {"provider": "tdx"}
        if symbols:
            kwargs["symbols"] = symbols
        if capability == "bars":
            kwargs.update(period="day", count=1)
        plan = QueryPlanner().compile(QuerySpec.build(capability, **kwargs))
        return QueryResult.from_plan(
            data if data is not None else [],
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def quotes(self, symbols, **kwargs):
        del kwargs
        values = [symbols] if isinstance(symbols, str) else list(symbols)
        return self._result(
            "quotes",
            symbols=values,
            data=[{"code": item, "price": 10.0} for item in values],
        )

    def bars(self, symbol, **kwargs):
        del kwargs
        return self._result("bars", symbols=symbol, data=[{"datetime": "2026-09-12"}])

    def snapshot(self, symbol, **kwargs):
        del kwargs
        return self._result("snapshot", symbols=symbol, data={"code": symbol})

    def minute(self, symbol, **kwargs):
        del kwargs
        return self._result("minute", symbols=symbol, data=[])

    def trades(self, symbol, **kwargs):
        del kwargs
        return self._result("trades", symbols=symbol, data=[])

    def security_count(self, **kwargs):
        del kwargs
        return self._result("security_count", data=1)

    def security_list(self, **kwargs):
        del kwargs
        return self._result("security_list", data=[{"code": "600519"}])

    def close(self):
        return None


def test_http_loopback_smoke():
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")

    from tstdx.integration.runtime_http import create_runtime_app

    app = create_runtime_app(SmokeClient())
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "uvicorn failed to start"
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/v13/runtime/health", timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 200
            assert body["status"] == "ok"
            assert body["api"] == "v13"

        with urllib.request.urlopen(
            f"{base}/v13/quotes?symbols=sh600519,sz000001", timeout=5
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["meta"]["provider"] == "tdx"
            assert [row["code"] for row in payload["data"]] == ["sh600519", "sz000001"]
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_ws_loopback_smoke():
    websockets = pytest.importorskip("websockets")
    pytest.importorskip("websockets.asyncio.server")

    from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler
    from tstdx.integration.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

    async def scenario() -> dict:
        server = await serve_runtime_ws(
            handler=RuntimeJsonRpcHandler(SmokeClient()),
            config=RuntimeWsConfig(host="127.0.0.1", port=0, path="/v13/ws"),
        )
        port = server.sockets[0].getsockname()[1]
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/v13/ws") as ws:
                await ws.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "quotes",
                            "params": {"symbols": ["sh600519"]},
                        }
                    )
                )
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        finally:
            server.close()
            await server.wait_closed()

    response = asyncio.run(scenario())
    assert response["id"] == 1
    assert response["result"]["meta"]["provider"] == "tdx"
    assert response["result"]["data"][0]["code"] == "sh600519"


def test_mcp_stdio_roundtrip_smoke():
    from tstdx.integration.mcp_server import MCPServer

    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        "",
    ]
    stdin_buf = io.StringIO("\n".join(requests) + "\n")
    stdout_buf = io.StringIO()

    server = MCPServer(client=SmokeClient())
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = stdin_buf, stdout_buf
    try:
        server.serve()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout

    lines = [line for line in stdout_buf.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    responses = [json.loads(line) for line in lines]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert all("result" in response or "error" in response for response in responses)
