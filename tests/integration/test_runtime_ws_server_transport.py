"""``serve_runtime_ws`` 的端到端判据：这张传输面自己那几行有没有真的做事（V19 第 28 轮第 3 遍）。

``RuntimeJsonRpcHandler`` 有三道判据守着（方法表 ⇄ 分派分支 ⇄ 入参白名单），HTTP/MCP/CLI
各有自己的投影表；偏偏这张**承载**它们的 WS 服务器一条都没有——它是
``atst.integration.__all__`` 里的公开出口、``docs/api/interfaces.md`` 里的托管入口，
从建好到本轮为止没有被任何测试连接过一次。射程外的四件事，每一件都在第 26 轮被认真改过：

一 业务调用必须离开事件循环线程（``asyncio.to_thread``）——否则一个慢请求拖死全部连接；
二 非规范路径必须被 1008 拒掉，而不是"顺手服务一下"；
三 通知（没有 ``id``）不得回帧；
四 自建的 handler 要登记在 ``server.atst_handler`` 上，宿主收尾时才关得到它；
  调用方传进来的那份不许登记（谁的所有权归谁，与 HTTP/MCP 面同一口径）。

这里全部走 127.0.0.1 上的临时端口：默认 8765 可能已被真实宿主占着，绑上去的判据
会在别人机器上以"连上了错误的服务"这种假绿通过。每个 await 都带墙钟上界。
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import atst.integration.runtime_ws_server as ws_server
from atst.integration.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

#: 每个 await 的墙钟上界：判据宁可超时红，也不许把测试会话挂死。
BOUND = 5.0
SLOW_SECONDS = 0.3


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StubHandler:
    """替身：只记账"谁在哪个线程上被叫到、叫了几次"，并原样回一帧。"""

    def __init__(self, *, slow: float = 0.0) -> None:
        self.slow = slow
        self.calls: list[int] = []
        self.closed = 0

    def handle_message(self, raw: str | bytes) -> str | None:
        self.calls.append(threading.get_ident())
        message = json.loads(raw)
        if self.slow:
            time.sleep(self.slow)
        if message.get("id") is None:  # 与真处理器同口径：通知不回帧
            return None
        return json.dumps(
            {"jsonrpc": "2.0", "id": message.get("id"), "result": {"stub": message.get("method")}}
        )

    def close(self) -> None:
        self.closed += 1


async def _serve(handler: Any, port: int) -> Any:
    server = await serve_runtime_ws(handler=handler, config=RuntimeWsConfig(port=port))
    return server


async def _round_trip(port: int, payload: dict[str, Any]) -> str | None:
    async with connect(f"ws://127.0.0.1:{port}/v13/ws") as websocket:
        await websocket.send(json.dumps(payload))
        if payload.get("id") is None:
            # 通知：期望**没有**应答帧。recv() 只能以超时收场。
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(websocket.recv(), timeout=BOUND / 5)
            return None
        return str(await asyncio.wait_for(websocket.recv(), timeout=BOUND))


@pytest.mark.asyncio
async def test_a_request_round_trips_through_the_transport() -> None:
    """判据零：面真的连得上、答得出，且应答按方法名回到调用方。"""
    stub = StubHandler()
    port = _free_port()
    server = await _serve(stub, port)
    try:
        raw = await asyncio.wait_for(
            _round_trip(port, {"jsonrpc": "2.0", "id": 7, "method": "bars"}), BOUND
        )
    finally:
        server.close()
        await server.wait_closed()
    assert raw is not None
    reply = json.loads(raw)
    assert reply["id"] == 7 and reply["result"]["stub"] == "bars"
    assert stub.calls, "帧到了服务器，却没走到 handler：这条面是断的"


@pytest.mark.asyncio
async def test_business_work_does_not_run_on_the_event_loop_thread() -> None:
    """判据一（形状）：``handle_message`` 跑在别的线程上。"""
    stub = StubHandler()
    port = _free_port()
    server = await _serve(stub, port)
    try:
        await asyncio.wait_for(
            _round_trip(port, {"jsonrpc": "2.0", "id": 1, "method": "quotes"}), BOUND
        )
    finally:
        server.close()
        await server.wait_closed()
    loop_thread = threading.get_ident()  # pytest-asyncio 在主线程上跑循环
    assert stub.calls and all(ident != loop_thread for ident in stub.calls), (
        f"业务调用回到了事件循环线程上（{stub.calls} vs {loop_thread}）："
        "``asyncio.to_thread`` 那一层没了，一个慢请求就能拖死整条连接池"
    )


@pytest.mark.asyncio
async def test_a_slow_request_does_not_starve_the_event_loop() -> None:
    """判据一（行为）：一个 0.3 秒的慢请求期间，循环照常发心跳。

    形状判据能被 ``run_in_executor`` 之类的等价改写糊过去，这条不能：它要的是
    "同一个循环在业务调用挂着的时候还能推进别的任务"这件事本身。
    """
    stub = StubHandler(slow=SLOW_SECONDS)
    port = _free_port()
    server = await _serve(stub, port)
    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    heartbeat = asyncio.get_running_loop().create_task(_heartbeat())
    try:
        await asyncio.wait_for(
            _round_trip(port, {"jsonrpc": "2.0", "id": 1, "method": "quotes"}), BOUND
        )
    finally:
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat
        server.close()
        await server.wait_closed()
    assert ticks >= 3, f"慢请求期间循环只走了 {ticks} 拍（预期 ≥3）：业务调用又把事件循环占住了"


@pytest.mark.asyncio
async def test_a_non_canonical_path_is_refused_with_1008() -> None:
    """判据二：``/v13/ws`` 之外的路径不是"顺手也服务一下"，而是 1008 关闭。"""
    stub = StubHandler()
    port = _free_port()
    server = await _serve(stub, port)
    code: Any = None
    try:
        with pytest.raises(ConnectionClosed) as info:
            async with connect(f"ws://127.0.0.1:{port}/elsewhere") as websocket:
                await websocket.send('{"jsonrpc":"2.0","id":1,"method":"quotes"}')
                await asyncio.wait_for(websocket.recv(), timeout=BOUND)
        code = info.value.rcvd.code if info.value.rcvd is not None else None
    finally:
        server.close()
        await server.wait_closed()
    assert code == 1008, f"错误路径的关闭码是 {code}，不是 1008"
    assert stub.calls == [], "错误路径仍然被送到了业务处理器"


@pytest.mark.asyncio
async def test_a_notification_gets_no_frame_back() -> None:
    """判据三：没有 ``id`` 的请求不回帧（JSON-RPC 通知口径）。"""
    stub = StubHandler()
    port = _free_port()
    server = await _serve(stub, port)
    try:
        raw = await asyncio.wait_for(
            _round_trip(port, {"jsonrpc": "2.0", "method": "runtime.health"}), BOUND
        )
    finally:
        server.close()
        await server.wait_closed()
    assert raw is None
    assert len(stub.calls) == 1, "通知被丢弃了：处理器一次都没被叫到"


@pytest.mark.asyncio
async def test_handler_ownership_is_visible_to_the_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """判据四：自建的那份登记在 ``server.atst_handler``，传进来的那份不登记。

    ``__main__`` 宿主的 ``finally`` 就是按这个属性收尾的；属性名一旦改而登记没跟上，
    托管进程退出时会把那份 ``Client`` 的 socket 与心跳线程原地留下（第 26 轮 F-100）。
    """
    mine = StubHandler()
    monkeypatch.setattr(ws_server, "RuntimeJsonRpcHandler", lambda *a, **k: mine)
    port = _free_port()
    owned = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    borrowed = await _serve(StubHandler(), _free_port())
    try:
        assert getattr(owned, "atst_handler", None) is mine, (
            "自建的 handler 没登记：宿主收尾时关不到它"
        )
        assert not hasattr(borrowed, "atst_handler"), (
            "调用方交进来的 handler 被登记了：服务器会去关一份不归它所有的东西"
        )
    finally:
        for server in (owned, borrowed):
            server.close()
            await server.wait_closed()
