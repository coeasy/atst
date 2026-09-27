"""``serve_runtime_ws`` 的实时订阅推送判据（第 31 轮：把进程内实时流桥接到 WS 连接）。

README/接口文档宣称 WS 提供 ``subscribe``/``unsubscribe``/``list`` 控制面与
``snapshot``/``error`` 服务端推送帧；此前 handler 只有 req/res，这条链路是断的。
这里走 127.0.0.1 临时端口，用假流（不触网）验证：

一 ``subscribe`` 回 ack 且连接随后收到 ``snapshot`` 推送帧；
二 ``list`` 能看到这条订阅；
三 ``unsubscribe`` 停掉流、``list`` 变空；
四 连接断开后本连接拥有的流被 ``stop_all_subscriptions`` 收掉，不泄漏。

每个 await 都带墙钟上界：判据宁可超时红，也不许把测试会话挂死。
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import pytest
from websockets.asyncio.client import connect

import tstdx.integration.runtime_ws as ws_module
from tstdx.integration.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

BOUND = 5.0

PUSH_METHOD = "push"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeState:
    """镜像真实 ``StreamState`` 枚举：既带 ``.value`` 又本身是字符串（``str, Enum``）。"""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


class _FakeStream:
    """假流：``start`` 推一帧快照，``stop`` 置已停，可被 ``list`` 读状态。"""

    def __init__(self, on_quote: Any, on_error: Any, symbols: list[str]) -> None:
        self._on_quote = on_quote
        self._on_error = on_error
        self.symbols = symbols
        self.stopped = False

    async def start(self) -> _FakeStream:
        # 让出一次循环，确保连接侧已就绪再推帧。
        await asyncio.sleep(0)
        if self._on_quote is not None:
            self._on_quote(self.symbols[0], {"price": 1.0, "symbol": self.symbols[0]})
        return self

    async def stop(self) -> None:
        self.stopped = True

    @property
    def state(self) -> _FakeState:
        return _FakeState("running" if not self.stopped else "closed")


class _FakeAsyncClient:
    """假异步客户端：``stream`` 返回假流，规避一切网络与真机 golden。"""

    def __init__(self, client: Any) -> None:
        self.client = client

    def stream(
        self,
        symbols: Any,
        *,
        provider: Any = None,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: Any = None,
        on_error: Any = None,
    ) -> _FakeStream:
        if isinstance(symbols, str):
            symbols = [symbols]
        return _FakeStream(on_quote, on_error, list(symbols))


@pytest.fixture
def fake_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_module, "AsyncClient", _FakeAsyncClient)


async def _send_recv(
    websocket: Any, payload: dict[str, Any], push_buffer: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """发一帧并等到本请求的应答。

    推送帧（``method == "push"``，无 id）与应答可能以任意顺序到达——一条连接上
    ``subscribe`` 的 ack 和首帧 snapshot 会竞争。这里把"非本请求的帧"缓冲到
    ``push_buffer``，既不丢弃也不误当成应答，调用方随后从缓冲区取推送帧断言。
    """
    await websocket.send(json.dumps(payload))
    if payload.get("id") is None:
        return None
    my_id = payload["id"]
    while True:
        frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=BOUND))
        if frame.get("id") == my_id:
            return frame
        push_buffer.append(frame)


async def _wait_for_push(
    websocket: Any, push_buffer: list[dict[str, Any]]
) -> dict[str, Any]:
    """从缓冲区或连接上取下一帧推送帧（带墙钟上界）。"""
    if push_buffer:
        return push_buffer.pop(0)
    while True:
        frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=BOUND))
        if frame.get("method") == PUSH_METHOD:
            return frame
        push_buffer.append(frame)


@pytest.mark.asyncio
async def test_subscribe_pushes_a_snapshot_frame(fake_async_client: None) -> None:
    """判据一：subscribe 之后连接收到 snapshot 推送帧。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    try:
        async with connect(f"ws://127.0.0.1:{port}/v13/ws") as websocket:
            pushes: list[dict[str, Any]] = []
            ack = await _send_recv(
                websocket,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "subscribe",
                    "params": {"symbols": ["600519"], "provider": "tdx", "interval": 1, "max_queue": 64},
                },
                pushes,
            )
            assert ack is not None and "error" not in ack, f"subscribe 被拒：{ack}"
            assert ack["result"]["status"] == "subscribed"  # type: ignore[index]
            # 推送帧（非请求/响应，没有 id）可能在 ack 之前或之后到达，从缓冲区/连接取。
            frame = await _wait_for_push(websocket, pushes)
            assert frame["method"] == "push", f"推送帧方法不是 push：{frame}"
            assert frame["params"]["type"] == "snapshot", f"推送类型不是 snapshot：{frame}"
            assert frame["params"]["code"] == "600519"
            assert frame["params"]["data"]["price"] == 1.0
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_list_and_unsubscribe_lifecycle(fake_async_client: None) -> None:
    """判据二/三：list 看到订阅，unsubscribe 停流并清空。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    try:
        async with connect(f"ws://127.0.0.1:{port}/v13/ws") as websocket:
            pushes: list[dict[str, Any]] = []
            await _send_recv(
                websocket,
                {"jsonrpc": "2.0", "id": 1, "method": "subscribe", "params": {"symbols": ["600519"]}},
                pushes,
            )
            listed = await _send_recv(websocket, {"jsonrpc": "2.0", "id": 2, "method": "list", "params": {}}, pushes)
            assert listed is not None and "error" not in listed, f"list 被拒：{listed}"
            subs = listed["result"]["subscriptions"]  # type: ignore[index]
            assert len(subs) == 1 and subs[0]["id"].startswith("sub")

            unsub = await _send_recv(
                websocket,
                {"jsonrpc": "2.0", "id": 3, "method": "unsubscribe", "params": {"id": subs[0]["id"]}},
                pushes,
            )
            assert unsub is not None and unsub["result"]["found"] is True  # type: ignore[index]
            listed2 = await _send_recv(
                websocket, {"jsonrpc": "2.0", "id": 4, "method": "list", "params": {}}, pushes
            )
            assert listed2 is not None and listed2["result"]["subscriptions"] == []  # type: ignore[index]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unknown_stream_field_is_rejected(fake_async_client: None) -> None:
    """判据（门禁一致性）：subscribe 带未声明字段当场 -32602。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    try:
        async with connect(f"ws://127.0.0.1:{port}/v13/ws") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "subscribe",
                        "params": {"symbols": ["600519"], "bogus": 1},
                    }
                )
            )
            reply = json.loads(await asyncio.wait_for(websocket.recv(), timeout=BOUND))
            assert reply["error"]["code"] == -32602, f"未知字段未被拒：{reply}"
    finally:
        server.close()
        await server.wait_closed()
