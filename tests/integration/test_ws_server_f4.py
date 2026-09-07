"""F4/V4+V7：WebSocket 服务面测试（to_thread 隔离 / 配置接线 / 订阅推送 / 错误面）。

handler 级测试完全离线；serve_ws 级测试用本地回环 socket（无外网）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

pytestmark = pytest.mark.unit

from tstdx.integration.ws_server import (  # noqa: E402
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    JsonRpcHandler,
    WsConfig,
    serve_ws,
)


class FakeClient:
    def __init__(self) -> None:
        self.fail = False

    def quotes(self, codes):
        if self.fail:
            raise RuntimeError("secret socket detail")
        return [{"code": c, "price": 1.0} for c in codes]

    def bars(self, symbol, period="day", count=100):
        return [{"symbol": symbol}]

    def capital_changes(self, symbol):
        return [{"symbol": symbol, "category": "除权除息"}]

    def block_quotes(self, block_type=0, start=0):
        return [{"block_type": block_type, "start": start, "name": "人工智能"}]

    def security_list(self, market=0, start=0):
        return [{"market": market, "start": start, "code": "600000"}]


class FakeFacade:
    """门面替身：覆盖 N2 跨源方法（离线）。"""

    def adjusted_bars(self, symbol, *, method="qfq", period="day", count=320):
        return [{"symbol": symbol, "method": method, "period": period, "count": count}]

    def all_market(self, *, node="hs_a", page_size=80, max_pages=None, source="sina"):
        return [{"node": node, "source": source}]

    def board_list(self, board="concept"):
        return [{"board": board, "name": "人工智能"}]

    def board_members(self, node, *, page_size=100, max_pages=None):
        return [{"node": node, "code": "600000"}]

    def minute_klines(self, symbol, *, period="5min", count=240):
        return [{"symbol": symbol, "period": period, "count": count}]

    def f10(self, symbol, filename, *, route=None):
        return [{"title": "财务分析", "text": "净利润增长"}]

    def f10_catalog(self, symbol, *, route=None):
        return [{"title": "公司概况", "filename": "gsgk.dat"}]


def _req(method: str, params: dict | None = None, req_id: int = 1) -> str:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


# --------------------------------------------------------------------------- #
# handler 级（V6/V7）
# --------------------------------------------------------------------------- #
def test_unsubscribe_requires_hashable_elements():
    """unsubscribe 元素必须可哈希（str）——嵌套 list 不再 TypeError 逃逸。"""
    h = JsonRpcHandler(client=FakeClient())
    out = json.loads(h.handle_message(_req("unsubscribe", {"symbols": [["x"]]})))
    assert out["error"]["code"] == ERR_INVALID_PARAMS


def test_unsubscribe_mixed_hashable_ok():
    h = JsonRpcHandler(client=FakeClient())
    h.handle_message(_req("subscribe", {"symbols": ["a", "b"]}))
    out = json.loads(h.handle_message(_req("unsubscribe", {"symbols": ["a"]})))
    assert out["result"]["subscribed"] == ["b"]


def test_native_exception_returns_internal_error():
    """原生异常对外只回 internal error，不外泄细节（V6）。"""
    fc = FakeClient()
    fc.fail = True
    h = JsonRpcHandler(client=fc)
    out = json.loads(h.handle_message(_req("quotes", {"symbols": ["600519"]})))
    assert out["error"]["code"] == ERR_INTERNAL
    assert out["error"]["message"] == "internal error"
    assert "secret" not in json.dumps(out)


def test_tdx_error_keeps_code_and_message():
    from tstdx.errors import ConnectionFailed

    class FailClient(FakeClient):
        def quotes(self, codes):
            raise ConnectionFailed("主站不可达")

    h = JsonRpcHandler(client=FailClient())
    out = json.loads(h.handle_message(_req("quotes", {"symbols": ["600519"]})))
    assert out["error"]["code"] == -32000
    assert "E2" in out["error"]["message"]  # 保留 TdxError code


def test_subscription_snapshot_per_handler_independent():
    """订阅集按 handler 独立（每连接独立订阅集的基础语义）。"""
    a = JsonRpcHandler(client=FakeClient())
    b = JsonRpcHandler(client=FakeClient())
    json.loads(a.handle_message(_req("subscribe", {"symbols": ["600519"]}, req_id=1)))
    assert a.subscription_snapshot() == ["600519"]
    assert b.subscription_snapshot() == []  # 不共享污染


def test_ws_config_has_push_interval():
    cfg = WsConfig(push_interval=0.5)
    assert cfg.push_interval == 0.5
    assert cfg.path == "/ws"
    assert cfg.max_message == 1 << 20


# --------------------------------------------------------------------------- #
# serve_ws 级（真实 websockets，本地回环；V7 配置接线 + 订阅推送）
# --------------------------------------------------------------------------- #
def _run_ws_scenario() -> None:
    pytest.importorskip("websockets")
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    fc = FakeClient()

    async def scenario() -> None:
        cfg = WsConfig(host="127.0.0.1", port=0, path="/ws", max_message=128, push_interval=0.2)
        server = await serve_ws(handler=JsonRpcHandler(client=fc), config=cfg)
        assert server.sockets, "server should be listening"
        port = server.sockets[0].getsockname()[1]
        assert getattr(server, "_tstdx_push_task", None) is not None, (
            "push task must be attached for graceful close"
        )
        try:
            # 4) path 校验：错误路径握手被 403 拒绝（服务关闭前完成）
            with pytest.raises(Exception) as exc_info:
                async with connect(f"ws://127.0.0.1:{port}/nope"):
                    pass  # pragma: no cover —— 不应到达
            assert (
                "403" in str(exc_info.value)
                or getattr(getattr(exc_info.value, "response", None), "status_code", 0) == 403
            )

            # 1) 正确路径：握手 + JSON-RPC 往返
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
                await ws.send(_req("subscribe", {"symbols": ["600519"]}, req_id=1))
                ack = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert ack["result"]["subscribed"] == ["600519"]

                # 2) F3 断线补洞：订阅后立即推送 quote_snapshot 快照；
                # 3) 周期推送：push_interval 内推送 quote_update。
                # 二者顺序不保证（快照与轮询循环存在事件循环交叉的良性
                # 竞态），客户端按 method 分型消费即可。
                methods = set()
                for _ in range(2):
                    note = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    assert note["params"]["quote"]["code"] == "600519"
                    methods.add(note["method"])
                assert methods == {"quote_snapshot", "quote_update"}

                # 4) max_message 接线：>128 字节消息触发连接关闭（1009）
                await ws.send("x" * 500)
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 5)
        finally:
            server.close()
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(server.wait_closed(), 5)
            task = getattr(server, "_tstdx_push_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    asyncio.run(scenario())


def test_serve_ws_path_maxsize_and_push_loop():
    """V7 三合一：max_size 生效、path 403、订阅推送循环真实推送。"""
    _run_ws_scenario()


def test_serve_ws_per_connection_subscriptions() -> None:
    """两个连接订阅集互不污染（修共享污染）。"""
    pytest.importorskip("websockets")
    from websockets.asyncio.client import connect

    fc = FakeClient()

    async def scenario() -> None:
        cfg = WsConfig(host="127.0.0.1", port=0, path="/ws", push_interval=0.2)
        server = await serve_ws(handler=JsonRpcHandler(client=fc), config=cfg)
        port = server.sockets[0].getsockname()[1]
        try:
            async with connect(f"ws://127.0.0.1:{port}/ws") as ws_a:
                await ws_a.send(_req("subscribe", {"symbols": ["600519"]}, req_id=1))
                await asyncio.wait_for(ws_a.recv(), 5)  # ack
                async with connect(f"ws://127.0.0.1:{port}/ws") as ws_b:
                    await ws_b.send(_req("subscribe", {"symbols": ["000001"]}, req_id=1))
                    await asyncio.wait_for(ws_b.recv(), 5)  # ack
                    codes = set()
                    for _ in range(4):
                        note = json.loads(await asyncio.wait_for(ws_b.recv(), 5))
                        if note.get("method") == "quote_update":
                            codes.add(note["params"]["quote"]["code"])
                    assert codes == {"000001"}  # 只收到自己的订阅
        finally:
            server.close()
            task = getattr(server, "_tstdx_push_task", None)
            if task is not None:
                task.cancel()

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# N2：WS 方法面扩展（服务面 parity）—— 客户端方法 / 门面方法各带离线用例
# --------------------------------------------------------------------------- #
def _h(facade: FakeFacade | None = None) -> JsonRpcHandler:
    return JsonRpcHandler(client=FakeClient(), facade=facade or FakeFacade())


def _call(h: JsonRpcHandler, method: str, params: dict | None = None) -> Any:
    out = json.loads(h.handle_message(_req(method, params)))
    assert "error" not in out, out
    return out["result"]


def test_ws_capital_changes():
    """N2：capital_changes 走客户端。"""
    result = _call(_h(), "capital_changes", {"symbol": "sh600519"})
    assert result[0]["category"] == "除权除息"


def test_ws_block_quotes():
    """N2：block_quotes 走客户端（block_type/start 透传）。"""
    result = _call(_h(), "block_quotes", {"block_type": 1, "start": 3})
    assert result[0]["block_type"] == 1 and result[0]["start"] == 3


def test_ws_security_list():
    """N2：security_list 走客户端。"""
    result = _call(_h(), "security_list", {"market": 1, "start": 5})
    assert result[0]["market"] == 1 and result[0]["start"] == 5


def test_ws_adjusted_bars():
    """N2：adjusted_bars 走门面（method/period/count 透传）。"""
    result = _call(_h(), "adjusted_bars", {"symbol": "sh600519", "method": "hfq"})
    assert result[0]["method"] == "hfq" and result[0]["count"] == 320


def test_ws_all_market():
    """N2：all_market 走门面（source/node 透传）。"""
    result = _call(_h(), "all_market", {"node": "cyb", "source": "tencent"})
    assert result[0]["node"] == "cyb" and result[0]["source"] == "tencent"


def test_ws_board_list():
    result = _call(_h(), "board_list", {"board": "industry"})
    assert result[0]["board"] == "industry"


def test_ws_board_members():
    result = _call(_h(), "board_members", {"node": "gn_ai"})
    assert result[0]["node"] == "gn_ai"


def test_ws_minute_klines():
    result = _call(_h(), "minute_klines", {"symbol": "sh600519", "period": "15min", "count": 10})
    assert result[0]["period"] == "15min" and result[0]["count"] == 10


def test_ws_f10_download():
    """N2：f10_download → 门面 f10（下载 + 解析正文）。"""
    result = _call(_h(), "f10_download", {"symbol": "sh600519", "filename": "cwbj.dat"})
    assert result[0]["title"] == "财务分析"


def test_ws_f10_catalog():
    """N1/N2：f10_catalog 走门面。"""
    result = _call(_h(), "f10_catalog", {"symbol": "sh600519"})
    assert result[0]["title"] == "公司概况"
    assert result[0]["filename"] == "gsgk.dat"


def test_ws_n2_method_missing_params_rejected():
    """缺参 → -32602（同一错误映射）。"""
    h = _h()
    out = json.loads(h.handle_message(_req("adjusted_bars", {}, req_id=1)))
    assert out["error"]["code"] == ERR_INVALID_PARAMS
