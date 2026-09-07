# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""L5：socket 级冒烟测试（真实端口 / 真实 stdio / 真实并发连接）。

与 F4/V 系列的 handler 级、TestClient 级测试互补：本文件验证的是
「服务器真的在端口/stdio 上活着并响应」，全部走 127.0.0.1 回环或
进程内 stdio，无外网依赖。
"""

from __future__ import annotations

import io
import json
import sys
import threading
import time
import urllib.request

import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# HTTP 真实 loopback（uvicorn 真端口，TestClient 覆盖不到的路径）
# --------------------------------------------------------------------------- #
def test_http_loopback_smoke():
    """uvicorn 起真实回环端口 → HTTP GET 端到端往返。"""
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")

    from tstdx.integration.http_server import create_app

    class SmokeClient:
        def quotes(self, codes):
            return [{"code": c, "price": 10.0} for c in codes]

    app = create_app(client=SmokeClient())
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        # 等待 uvicorn 完成绑定（port=0 时由内核分配）
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started, "uvicorn 未能在 5s 内启动"
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/system/health", timeout=5) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            assert body["status"] == "ok"

        with urllib.request.urlopen(f"{base}/quotes?codes=600519,000001", timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))["data"]
            assert [q["code"] for q in data] == ["600519", "000001"]
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


# --------------------------------------------------------------------------- #
# WS 并发推送冒烟（两个并发客户端各自订阅、各自收到推送）
# --------------------------------------------------------------------------- #
def test_ws_concurrent_clients_push_smoke():
    """两个并发 WS 客户端同时订阅 → 各自独立收到推送帧。"""
    websockets = pytest.importorskip("websockets")
    pytest.importorskip("websockets.asyncio.server")

    import asyncio

    from tstdx.integration.ws_server import JsonRpcHandler, WsConfig, serve_ws

    class PushClient:
        def quotes(self, codes):
            return [{"code": c, "price": 1.0} for c in codes]

    async def scenario() -> list[str]:
        cfg = WsConfig(host="127.0.0.1", port=0, path="/ws")
        server = await serve_ws(handler=JsonRpcHandler(client=PushClient()), config=cfg)
        port = server.sockets[0].getsockname()[1]
        received: list[str] = []
        try:

            async def one_client(tag: str) -> None:
                async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "subscribe",
                                "params": {"symbols": [f"sh60051{tag}"], "interval": 0.1},
                            }
                        )
                    )
                    # 订阅 ack + 推送帧：读到 2 条即认为本客户端链路健康
                    for _ in range(2):
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                        received.append(f"{tag}:{msg.get('method', msg.get('id'))}")

            await asyncio.gather(one_client("0"), one_client("1"))
        finally:
            server.close()
            await server.wait_closed()
        return received

    got = asyncio.run(scenario())
    assert len(got) == 4, f"两个客户端各应收到 2 条消息，实际 {got!r}"


# --------------------------------------------------------------------------- #
# MCP stdio 往返（进程内替换 sys.stdin/stdout 的真实行协议往返）
# --------------------------------------------------------------------------- #
def test_mcp_stdio_roundtrip_smoke():
    """serve() 主循环：stdin 喂 JSON-RPC 行 → stdout 收到合法响应行。"""
    from tstdx.integration.mcp_server import MCPServer

    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
        "",  # EOF 结束循环
    ]
    stdin_buf = io.StringIO("\n".join(requests) + "\n")
    stdout_buf = io.StringIO()

    server = MCPServer(client=None)  # 不触发真实客户端构造
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = stdin_buf, stdout_buf
    try:
        server.serve()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout

    lines = [ln for ln in stdout_buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 3, f"应收到 3 条响应，实际 {len(lines)}"
    responses = [json.loads(ln) for ln in lines]
    assert [r["id"] for r in responses] == [1, 2, 3]
    for r in responses:
        assert "result" in r or "error" in r
