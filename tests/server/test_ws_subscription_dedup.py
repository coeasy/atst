"""F-8 回归：等价订阅共享同一条上游轮询，投递仍然按连接。

``docs/REFACTOR_PLAN.md`` 的 F-8 指出：同一个 ``(symbols, provider)`` 被重复订阅时
每条订阅各起一条轮询，N 条连接看同一批标的就有 N 条上游轮询。这里验证合流后的
四件事：

一 两条连接订阅同一批标的只起一条流（数 ``AsyncClient.stream`` 的调用）；
二 两条连接都收到推送帧（后加入的那条先拿到当前快照的回放）；
三 全部退订后流被停、登记表里没有残留，重复退订/退订后再断连都幂等；
四 一条连接的发送口坏掉只摘它自己的订阅，另一条的上游流不受影响。

每个 await 都带墙钟上界：判据宁可超时红，也不许把测试会话挂死。
"""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import pytest
from websockets.asyncio.client import connect

import atst.server.runtime_ws as ws_module
from atst.server.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

BOUND = 5.0
PUSH_METHOD = "push"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FakeState:
    """镜像真实 ``StreamState`` 枚举：带 ``.value`` 且可被 ``list`` 读出。"""

    def __init__(self, value: str) -> None:
        self.value = value


class _CountingStream:
    """假流：``start`` 推一帧快照，之后由判据手动 ``push``；记录建了几条、跑了几次。"""

    instances: list[_CountingStream] = []

    def __init__(self, on_quote: Any, on_error: Any, symbols: list[str]) -> None:
        self._on_quote = on_quote
        self._on_error = on_error
        self.symbols = list(symbols)
        self.starts = 0
        self.stopped = False
        self.pushes = 0
        type(self).instances.append(self)

    async def start(self) -> _CountingStream:
        self.starts += 1
        await asyncio.sleep(0)
        self.push(1.0)
        return self

    def push(self, price: float) -> None:
        """模拟一个轮询节拍：把同一帧分发给流上挂着的回调。"""
        self.pushes += 1
        for symbol in self.symbols:
            self._on_quote(symbol, {"price": price, "symbol": symbol})

    async def stop(self) -> None:
        self.stopped = True

    @property
    def state(self) -> _FakeState:
        return _FakeState("closed" if self.stopped else "running")


class _CountingAsyncClient:
    """假异步客户端：数 ``stream`` 被调用几次——即上游轮询被建了几条。"""

    calls = 0

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
    ) -> _CountingStream:
        type(self).calls += 1
        if isinstance(symbols, str):
            symbols = [symbols]
        return _CountingStream(on_quote, on_error, list(symbols))


@pytest.fixture
def counting_streams(monkeypatch: pytest.MonkeyPatch) -> type[_CountingStream]:
    _CountingStream.instances.clear()
    _CountingAsyncClient.calls = 0
    monkeypatch.setattr(ws_module, "AsyncClient", _CountingAsyncClient)
    yield _CountingStream
    _CountingStream.instances.clear()


def _subscribe_msg(request_id: int, symbols: list[str], provider: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "subscribe",
        "params": {"symbols": symbols, "provider": provider, "interval": 1, "max_queue": 64},
    }


async def _send_recv(
    websocket: Any, payload: dict[str, Any], push_buffer: list[dict[str, Any]]
) -> dict[str, Any]:
    """发一帧并等到本请求的应答；推送帧按到达顺序进缓冲区。"""
    await websocket.send(json.dumps(payload))
    my_id = payload["id"]
    while True:
        frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=BOUND))
        if frame.get("id") == my_id:
            return frame
        push_buffer.append(frame)


async def _wait_for_push(websocket: Any, push_buffer: list[dict[str, Any]]) -> dict[str, Any]:
    if push_buffer:
        return push_buffer.pop(0)
    while True:
        frame = json.loads(await asyncio.wait_for(websocket.recv(), timeout=BOUND))
        if frame.get("method") == PUSH_METHOD:
            return frame
        push_buffer.append(frame)


def _poller_for(symbols: list[str]) -> Any:
    matches = [p for p in ws_module._POLLERS.values() if p.symbols == symbols]
    assert len(matches) == 1, f"期望恰好一条共享轮询，实际 {len(matches)}"
    return matches[0]


@pytest.mark.asyncio
async def test_two_connections_share_one_upstream_poll_loop(
    counting_streams: type[_CountingStream],
) -> None:
    """判据一/二：两条连接订阅同一批标的只起一条流，但两路都收到帧。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    url = f"ws://127.0.0.1:{port}/v13/ws"
    try:
        async with connect(url) as first, connect(url) as second:
            first_pushes: list[dict[str, Any]] = []
            second_pushes: list[dict[str, Any]] = []

            ack_a = await _send_recv(first, _subscribe_msg(1, ["600519"], "tdx"), first_pushes)
            assert "error" not in ack_a, f"subscribe 被拒：{ack_a}"
            frame_a = await _wait_for_push(first, first_pushes)
            assert frame_a["params"]["type"] == "snapshot"
            assert frame_a["params"]["data"]["price"] == 1.0
            assert frame_a["params"]["meta"]["provider"] == "tdx"

            ack_b = await _send_recv(second, _subscribe_msg(2, ["600519"], "tdx"), second_pushes)
            assert "error" not in ack_b, f"subscribe 被拒：{ack_b}"

            # 判据一：上游只有一条流，第二条订阅挂在同一条 poller 上。
            assert _CountingAsyncClient.calls == 1, "同一 (symbols, provider) 起了多条上游轮询"
            assert len(counting_streams.instances) == 1
            stream = counting_streams.instances[0]
            assert stream.starts == 1, "共享 poller 被重复 start"

            # 判据二（后加入者）：回放当前快照，不必等下一个轮询节拍。
            replayed = await _wait_for_push(second, second_pushes)
            assert replayed["params"]["code"] == "600519"
            assert replayed["params"]["data"]["price"] == 1.0

            # 判据二：一个轮询节拍两路都收到。
            stream.push(2.0)
            pushed_a = await _wait_for_push(first, first_pushes)
            pushed_b = await _wait_for_push(second, second_pushes)
            assert pushed_a["params"]["data"]["price"] == 2.0
            assert pushed_b["params"]["data"]["price"] == 2.0
            assert pushed_a["params"]["meta"]["provider"] == "tdx"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unsubscribing_everyone_cancels_the_shared_poller(
    counting_streams: type[_CountingStream],
) -> None:
    """判据三：引用计数归零才停流；重复退订幂等，登记表不留条目。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    url = f"ws://127.0.0.1:{port}/v13/ws"
    try:
        async with connect(url) as first, connect(url) as second:
            first_pushes: list[dict[str, Any]] = []
            second_pushes: list[dict[str, Any]] = []
            ack_a = await _send_recv(first, _subscribe_msg(1, ["600519"], "tdx"), first_pushes)
            ack_b = await _send_recv(second, _subscribe_msg(2, ["600519"], "tdx"), second_pushes)
            sub_a = ack_a["result"]["subscription_id"]
            sub_b = ack_b["result"]["subscription_id"]

            poller = _poller_for(["600519"])
            stream = counting_streams.instances[0]
            assert poller.refcount == 2

            unsub_a = await _send_recv(
                first,
                {"jsonrpc": "2.0", "id": 3, "method": "unsubscribe", "params": {"id": sub_a}},
                first_pushes,
            )
            assert unsub_a["result"]["found"] is True
            # 还有一条连接在听：上游流不停。
            assert poller.refcount == 1
            assert not stream.stopped, "最后一个订阅者还没走就把共享流停了"

            # 重复退订：幂等，不重复减计数。
            again = await _send_recv(
                first,
                {"jsonrpc": "2.0", "id": 4, "method": "unsubscribe", "params": {"id": sub_a}},
                first_pushes,
            )
            assert again["result"]["found"] is False
            assert poller.refcount == 1

            unsub_b = await _send_recv(
                second,
                {"jsonrpc": "2.0", "id": 5, "method": "unsubscribe", "params": {"id": sub_b}},
                second_pushes,
            )
            assert unsub_b["result"]["found"] is True
            # 判据三：计数归零 → 流被停、登记项被摘。
            assert poller.refcount == 0
            assert poller.closed
            assert stream.stopped, "引用计数归零后上游轮询没有停"
            assert poller.key not in ws_module._POLLERS
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_after_unsubscribe_leaves_no_orphan(
    counting_streams: type[_CountingStream],
) -> None:
    """判据三（幂等）：退订后再断连，连接收尾不会二次停流、也不会留下登记项。"""
    port = _free_port()
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=port))
    url = f"ws://127.0.0.1:{port}/v13/ws"
    stream: _CountingStream | None = None
    try:
        async with connect(url) as websocket:
            pushes: list[dict[str, Any]] = []
            ack = await _send_recv(websocket, _subscribe_msg(1, ["600519"], "tdx"), pushes)
            sub_id = ack["result"]["subscription_id"]
            await _send_recv(
                websocket,
                {"jsonrpc": "2.0", "id": 2, "method": "unsubscribe", "params": {"id": sub_id}},
                pushes,
            )
            stream = counting_streams.instances[0]
            assert stream.stopped
            assert not ws_module._POLLERS
        # 连接已关：服务端 finally 里还会再排空一次（空表），不许抛也不许复活流。
        await asyncio.sleep(0)
        assert not ws_module._POLLERS
        assert stream.stopped
    finally:
        server.close()
        await server.wait_closed()


class _ClientSentinel:
    """handler 只把 client 当共享键的身份；假 ``AsyncClient`` 不读它的内容。"""


@pytest.mark.asyncio
async def test_dead_connection_only_drops_itself(
    counting_streams: type[_CountingStream],
) -> None:
    """判据四：一条连接发送失败只摘它的订阅，另一条的上游流照常推。"""
    loop = asyncio.get_running_loop()
    client = _ClientSentinel()
    dead = ws_module.RuntimeJsonRpcHandler(client=client)
    alive = ws_module.RuntimeJsonRpcHandler(client=client)
    delivered: list[str] = []

    async def _bad_send(_payload: str) -> None:
        raise RuntimeError("socket is gone")

    async def _good_send(payload: str) -> None:
        delivered.append(payload)

    dead.bind_connection(loop, _bad_send)
    alive.bind_connection(loop, _good_send)
    try:
        await dead._subscribe(["600519"], "tdx", 1.0, False, 64)
        sub_alive = await alive._subscribe(["600519"], "tdx", 1.0, False, 64)
        assert _CountingAsyncClient.calls == 1, "两条连接没有共享同一条上游轮询"
        stream = counting_streams.instances[0]
        poller = _poller_for(["600519"])

        # 初始帧已经让坏连接失败：只摘它自己，流与另一条订阅都还在。
        for _ in range(10):
            await asyncio.sleep(0)
        assert dead._subs == {}, "发送失败的连接没有被摘掉"
        assert sub_alive in alive._subs, "另一条连接的订阅被连带摘掉"
        assert poller.refcount == 1
        assert not stream.stopped, "一条连接坏掉就把共享上游流停了"

        delivered.clear()
        stream.push(3.0)
        for _ in range(10):
            await asyncio.sleep(0)
        assert delivered, "存活连接没有收到共享 poller 的帧"
        frame = json.loads(delivered[-1])
        assert frame["params"]["data"]["price"] == 3.0
    finally:
        await dead.stop_all_subscriptions()
        await alive.stop_all_subscriptions()

    assert poller.closed
    assert stream.stopped
    assert poller.key not in ws_module._POLLERS
