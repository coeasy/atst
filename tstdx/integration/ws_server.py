# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebSocket 行情服务（C2）—— JSON-RPC 2.0 over ``/ws``。

设计要点
--------
* **协议处理与传输解耦**：:class:`JsonRpcHandler.handle_message` 是纯函数
  （输入原始消息 → 输出 JSON 响应或 ``None``），不接触 websocket 对象，
  可完全离线单测；:func:`serve_ws` 只负责把 socket 读写接到 handler 上。
* **方法集（N2 扩展）**：``bars / quotes / minute / trades / finance /
  security_count / stock_changes / capital_changes / adjusted_bars /
  block_quotes / all_market / board_list / board_members / security_list /
  minute_klines / f10_download / f10_catalog / subscribe / unsubscribe``。
  除权除息 / 板块行情 / 证券列表直接复用客户端方法；复权 / 全市场 / 板块 /
  分钟 K 线 / F10 走门面 :class:`~tstdx.facade.api.UnifiedQuoteAPI`（自动
  路由 + 连接管理），与 HTTP 服务面能力对齐（N2 服务面 parity）。
* **错误码**：``-32601`` 方法不存在；``-32602`` 参数非法；``-32700`` JSON 解析失败。
  原生异常对外只回 ``internal error``，细节进日志（V6）。
* **订阅**：``subscribe`` 登记符号到**每连接独立**的订阅集，由 :func:`serve_ws`
  内的后台推送循环按 :attr:`WsConfig.push_interval`（默认 3 秒）轮询
  ``client.quotes``（经 ``asyncio.to_thread``，不阻塞事件循环）并推送
  ``quote_update`` 通知。
* **配置真实接线**（V7）：``WsConfig.max_message`` → ``websockets.serve(max_size=)``；
  ``WsConfig.path`` → 握手期路径校验，不匹配直接 403 拒绝升级。
* **阻塞调用隔离**（V4）：handler 内的同步 TdxClient 调用一律
  ``asyncio.to_thread`` 包裹——一次 TDX 超时/换主站重试不再卡死全部连接。
* **零强制依赖**：``websockets``/``fastapi`` 仅在真正启动服务时导入。

.. warning:: 安全与受信假设（P8）
   本服务面**无鉴权/授权**——所有能连上 ``/ws`` 的客户端都可以查询行情、
   订阅推送，并调用任何暴露方法。这是**受信网络假设**：默认只应绑定
   本地回环（``127.0.0.1``）或受信内网，**不要**直接暴露到公网或不可信
   网络。需要对外部署时，请在前置网关（反向代理 / API 网关）加认证与
   TLS，或为服务增加 token 守卫（当前版本未内置）。

订阅与推送协议（F3 文档化）
--------------------------
* 订阅：客户端先发 ``subscribe``，如 ``{"jsonrpc":"2.0","id":1,"method":
  "subscribe","params":{"symbols":["sh600519","sz000001"]}}``，返回当前订阅集
  ``{"subscribed": [...]}``；``unsubscribe`` 退订同构。
* 服务端 → 客户端有两类行情通知（均为 ``method`` + ``params`` 形式的 JSON-RPC
  notification，无 ``id``，不回包）：

  * ``quote_update``（周期推送）：推送循环按 :attr:`WsConfig.push_interval`
    （默认 3 秒）轮询订阅符号，逐符号推送
    ``{"method":"quote_update","params":{"quote": {...}}}``；
  * ``quote_snapshot``（断线补洞）：连接刚收到 ``subscribe`` 时**立即**推送
    新增订阅符号的全量快照（对齐 R4「重连后立即定向补拉」）。客户端断线
    重连后重新 ``subscribe``，无需等下一轮 ``quote_update`` 即可恢复最新
    行情——漏推区间即被快照补齐。

* 背压语义：服务端对慢客户端不设应用层队列缓存——单连接推送失败仅记日志
  并跳过该连接（见 :func:`serve_ws` 推送循环），不拖垮其他连接；客户端应
  尽快消费，避免 TCP 缓冲堆积。
* Gap 语义：服务端不追踪逐笔增量，行情按「周期全量行」推送；客户端断线
  窗口内漏收若干轮后，重连以 ``quote_snapshot`` 对齐即可。WS 服务不存在
  streaming 引擎的 ``GapUnfilledError`` 级「连续缺失」概念。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Callable, Iterable
from typing import Any

from ..errors import TdxError

__all__ = ["JsonRpcHandler", "serve_ws", "WsConfig"]

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

#: 服务端 → 客户端通知方法名（F3：协议文档化）。
NOTIFY_QUOTE_UPDATE = "quote_update"  # 周期推送（push 循环）
NOTIFY_QUOTE_SNAPSHOT = "quote_snapshot"  # 断线补洞（subscribe 即推快照）


def _error(code: int, message: str, req_id: Any = None) -> dict[str, Any]:  # noqa: ANN401
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": {"code": code, "message": message}}


def _result(result: Any, req_id: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


class WsConfig:
    """WebSocket 服务配置。

    Attributes
    ----------
    host / port:
        监听地址与端口（默认仅绑 ``127.0.0.1:8765``）。
    path:
        握手路径白名单（默认 ``/ws``）——不匹配的握手请求直接 403
        拒绝升级（V7：此前该字段未接线，任何路径都能握手成功）。
    max_message:
        单条入站消息最大字节数（V7：接线到 ``websockets.serve(max_size=)``）。
    push_interval:
        订阅行情推送轮询间隔（秒，默认 3.0；``<= 0`` 视为 3.0）。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/ws",
        max_message: int = 1 << 20,
        push_interval: float = 3.0,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.max_message = max_message
        self.push_interval = push_interval


class JsonRpcHandler:
    """JSON-RPC 2.0 消息处理器（与传输无关，可离线测试）。

    Parameters
    ----------
    client:
        行情客户端（鸭子类型）。``None`` 时懒加载 :class:`~tstdx.client.TdxClient`。
    facade:
        门面（:class:`~tstdx.facade.api.UnifiedQuoteAPI`）——复权 / 全市场 /
        板块 / 分钟 K 线 / F10 等跨源方法使用。``None`` 时懒加载。
    """

    METHODS: tuple[str, ...] = (
        "bars",
        "quotes",
        "minute",
        "trades",
        "finance",
        "security_count",
        "stock_changes",
        "capital_changes",
        "adjusted_bars",
        "block_quotes",
        "all_market",
        "board_list",
        "board_members",
        "security_list",
        "minute_klines",
        "f10_download",
        "f10_catalog",
        "subscribe",
        "unsubscribe",
    )

    def __init__(self, client: Any = None, facade: Any = None) -> None:  # noqa: ANN401
        self._client = client
        self._facade = facade
        self._lock = threading.Lock()
        self.subscriptions: set[str] = set()

    # -- 消息入口 ------------------------------------------------------------ #
    def handle_message(self, raw: str | bytes) -> str | None:
        """处理一条入站消息，返回要回发的 JSON 文本；通知类消息返回 ``None``。"""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            msg = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json.dumps(_error(ERR_PARSE, "parse error"), ensure_ascii=False)

        if isinstance(msg, list):  # 批量
            replies = [r for r in (self._handle_one(m) for m in msg) if r is not None]
            return json.dumps(replies, ensure_ascii=False) if replies else None
        reply = self._handle_one(msg)
        return json.dumps(reply, ensure_ascii=False) if reply is not None else None

    def on_message(self, raw: str | bytes) -> tuple[str | None, list[str]]:
        """处理入站消息，并报告本次**新增**订阅符号（F3：断线补洞触发点）。

        Returns
        -------
        ``(reply_text, added_symbols)``——``added_symbols`` 为非空时，传输层
        应立即为这些符号推送 ``quote_snapshot`` 快照（订阅/重连即对齐）。
        """
        before = set(self.subscriptions)
        reply = self.handle_message(raw)
        added = sorted(set(self.subscriptions) - before)
        return reply, added

    def _handle_one(self, msg: Any) -> dict[str, Any] | None:  # noqa: ANN401
        if (
            not isinstance(msg, dict)
            or msg.get("jsonrpc") != JSONRPC_VERSION
            or "method" not in msg
        ):
            return _error(
                ERR_INVALID_REQUEST,
                "invalid request",
                msg.get("id") if isinstance(msg, dict) else None,
            )

        method = msg["method"]
        req_id = msg.get("id")
        params = msg.get("params") or {}

        # 通知（无 id）→ 不回包
        if req_id is None:
            # 通知失败静默（JSON-RPC 规范）
            with contextlib.suppress(TdxError, ValueError, KeyError, TypeError):
                self._dispatch(method, params)
            return None

        try:
            result = self._dispatch(method, params)
        except _MethodNotFound:
            return _error(ERR_METHOD_NOT_FOUND, f"method not found: {method}", req_id)
        except _InvalidParams as exc:
            return _error(ERR_INVALID_PARAMS, str(exc), req_id)
        except TdxError as exc:
            # TdxError 属预期错误：保留 code 与 message
            return _error(-32000, f"[{exc.code}] {exc}", req_id)
        except Exception as exc:  # noqa: BLE001 —— 原生异常不外泄细节（V6）
            logger.error("ws_server dispatch failed (method=%s): %s", method, exc, exc_info=exc)
            return _error(ERR_INTERNAL, "internal error", req_id)
        return _result(result, req_id)

    # -- 分发 ---------------------------------------------------------------- #
    def _dispatch(self, method: str, params: Any) -> Any:  # noqa: ANN401
        if method not in self.METHODS:
            raise _MethodNotFound(method)
        handler: Callable[[Any], Any] = getattr(self, f"_m_{method}")
        return handler(params)

    def _client_obj(self) -> Any:  # noqa: ANN401
        if self._client is None:
            from ..client import TdxClient

            self._client = TdxClient()
        return self._client

    def _facade_obj(self) -> Any:  # noqa: ANN401
        """门面（:class:`~tstdx.facade.api.UnifiedQuoteAPI`），惰性创建。

        复权 / 全市场 / 板块 / 分钟 K 线 / F10 等跨源方法统一走门面，
        复用其自动路由与连接管理；单连接独立实例（测试注入 fake 即可离线）。
        """
        if self._facade is None:
            from ..facade.api import UnifiedQuoteAPI

            self._facade = UnifiedQuoteAPI()
        return self._facade

    # -- 各方法（params 一律 dict 形式） --------------------------------------- #
    @staticmethod
    def _require(params: dict, *keys: str) -> None:
        missing = [k for k in keys if k not in params]
        if missing:
            raise _InvalidParams(f"missing params: {', '.join(missing)}")

    def _m_bars(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbol")
        return self._client_obj().bars(
            params["symbol"],
            period=params.get("period", "day"),
            count=params.get("count", 100),
        )

    def _m_quotes(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbols")
        return self._client_obj().quotes(params["symbols"])

    def _m_minute(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbol")
        return self._client_obj().minute_today(params["symbol"])

    def _m_trades(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbol")
        return self._client_obj().trade_today(params["symbol"])

    def _m_finance(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbol")
        return self._client_obj().finance_info(params["symbol"])

    def _m_security_count(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "market")
        return self._client_obj().security_count(params["market"])

    def _m_stock_changes(self, params: dict) -> Any:  # noqa: ANN401
        """盘中异动池（Web 源；交易时段实时，非交易时段空列表为合法）。"""
        from ..web.facade import WebQuoteSession

        raw = params.get("types") or []
        if not isinstance(raw, (list, tuple)) or not all(isinstance(t, int) for t in raw):
            raise _InvalidParams("types must be a list of integers (e.g. [8201, 8193])")
        return WebQuoteSession.stock_changes(
            tuple(raw), page=int(params.get("page", 1)), size=int(params.get("size", 50))
        )

    # -- N2 方法面扩展（服务面 parity；capital/板块/证券列表走客户端，
    #    复权/全市场/板块/分钟K线/F10 走门面） -------------------------------- #
    def _m_capital_changes(self, params: dict) -> Any:  # noqa: ANN401
        """除权除息 / 股本变迁（0x000F）。"""
        self._require(params, "symbol")
        return self._client_obj().capital_changes(params["symbol"])

    def _m_adjusted_bars(self, params: dict) -> Any:  # noqa: ANN401
        """复权 K 线（门面：原始 K 线 + 除权除息事件 → 前/后/定点复权）。"""
        self._require(params, "symbol")
        return self._facade_obj().adjusted_bars(
            params["symbol"],
            method=params.get("method", "qfq"),
            period=params.get("period", "day"),
            count=int(params.get("count", 320)),
        )

    def _m_block_quotes(self, params: dict) -> Any:  # noqa: ANN401
        """板块行情（0x07E5；block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数）。"""
        return self._client_obj().block_quotes(
            int(params.get("block_type", 0)), int(params.get("start", 0))
        )

    def _m_all_market(self, params: dict) -> Any:  # noqa: ANN401
        """全市场行情摘要（门面 web 路由：sina/tencent）。"""
        return self._facade_obj().all_market(
            node=params.get("node", "hs_a"),
            page_size=int(params.get("page_size", 80)),
            max_pages=params.get("max_pages"),
            source=params.get("source", "sina"),
        )

    def _m_board_list(self, params: dict) -> Any:  # noqa: ANN401
        """新浪板块列表（concept 概念 / region 地域 / industry 行业）。"""
        return self._facade_obj().board_list(board=params.get("board", "concept"))

    def _m_board_members(self, params: dict) -> Any:  # noqa: ANN401
        """板块成分行情（新浪 node 分页）。"""
        self._require(params, "node")
        return self._facade_obj().board_members(
            params["node"],
            page_size=int(params.get("page_size", 100)),
            max_pages=params.get("max_pages"),
        )

    def _m_security_list(self, params: dict) -> Any:  # noqa: ANN401
        """证券代码表（0x044D 分页）。"""
        return self._client_obj().security_list(
            params.get("market", 0), int(params.get("start", 0))
        )

    def _m_minute_klines(self, params: dict) -> Any:  # noqa: ANN401
        """分钟 K 线（门面 web 路由：A 股腾讯 mkline；港美东财 push2his）。"""
        self._require(params, "symbol")
        return self._facade_obj().minute_klines(
            params["symbol"],
            period=params.get("period", "5min"),
            count=int(params.get("count", 240)),
        )

    def _m_f10_download(self, params: dict) -> Any:  # noqa: ANN401
        """F10 栏目正文下载 + 文本解析（门面）。"""
        self._require(params, "symbol", "filename")
        return self._facade_obj().f10(params["symbol"], params["filename"])

    def _m_f10_catalog(self, params: dict) -> Any:  # noqa: ANN401
        """F10 栏目目录（0x0001，门面）。"""
        self._require(params, "symbol")
        return self._facade_obj().f10_catalog(params["symbol"])

    def _m_subscribe(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbols")
        symbols = params["symbols"]
        if not isinstance(symbols, (list, tuple)) or not all(isinstance(s, str) for s in symbols):
            raise _InvalidParams("symbols must be a list of strings")
        with self._lock:
            self.subscriptions.update(symbols)
        return {"subscribed": sorted(self.subscriptions)}

    def _m_unsubscribe(self, params: dict) -> Any:  # noqa: ANN401
        self._require(params, "symbols")
        symbols = params["symbols"]
        if not isinstance(symbols, (list, tuple)) or not all(isinstance(s, str) for s in symbols):
            # 元素必须可哈希（str），否则 difference_update 抛 TypeError
            raise _InvalidParams("symbols must be a list of strings")
        with self._lock:
            self.subscriptions.difference_update(symbols)
        return {"subscribed": sorted(self.subscriptions)}

    def subscription_snapshot(self) -> list[str]:
        """当前订阅集快照（锁内拷贝，供推送循环消费）。"""
        with self._lock:
            return sorted(self.subscriptions)

    # -- 服务端推送 ------------------------------------------------------------ #
    def build_notification(self, method: str, params: Any) -> str:  # noqa: ANN401
        """构造服务端 → 客户端的 notification 文本。"""
        return json.dumps(
            {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params},
            ensure_ascii=False,
        )

    def push_quote_updates(self, symbols: Iterable[str] | None = None) -> list[str]:
        """为当前订阅生成 ``quote_update`` 行情通知（推送循环调用）。"""
        syms = list(symbols) if symbols is not None else sorted(self.subscriptions)
        if not syms:
            return []
        try:
            rows = self._client_obj().quotes(syms)
        except TdxError:
            return []
        return [self.build_notification(NOTIFY_QUOTE_UPDATE, {"quote": row}) for row in rows or []]

    def push_snapshot(self, symbols: Iterable[str]) -> list[str]:
        """为新增订阅符号生成 ``quote_snapshot`` 补洞通知（F3）。

        订阅 / 重连后立即推送当前行情快照，客户端无需等下一轮
        ``quote_update`` 即可对齐断线窗口内漏推的区间。
        """
        syms = list(symbols)
        if not syms:
            return []
        try:
            rows = self._client_obj().quotes(syms)
        except TdxError:
            return []
        return [
            self.build_notification(NOTIFY_QUOTE_SNAPSHOT, {"quote": row}) for row in rows or []
        ]


class _MethodNotFound(Exception):
    pass


class _InvalidParams(Exception):
    pass


# --------------------------------------------------------------------------- #
# 传输层（真实 websocket 服务器）
# --------------------------------------------------------------------------- #
def _import_ws_serve() -> tuple[Callable[..., Any], bool]:
    """导入 ``websockets`` 服务入口；返回 ``(serve, is_modern_api)``。

    websockets >= 13 走 ``websockets.asyncio.server.serve``（新 asyncio
    实现：handler(connection)、process_request(connection, request)→Response）；
    更老版本回退到 ``websockets.serve``（legacy：handler(ws, path)、
    process_request(path, headers)→tuple）。
    """
    try:
        from websockets.asyncio.server import serve  # noqa: F401

        return serve, True
    except ImportError:  # pragma: no cover —— 老版本 websockets
        from websockets import serve

        return serve, False


def serve_ws(
    handler: JsonRpcHandler | None = None,
    config: WsConfig | None = None,
) -> Any:  # pragma: no cover —— 需要真实 socket
    """启动 WebSocket 服务（``websockets`` 库；返回可 await 的 Server 协程）。

    接线说明（V4/V7）：

    * 每个连接一个独立 :class:`JsonRpcHandler`（独立订阅集，修共享污染），
      底层客户端与 ``handler`` 参数（作为模板）共享；
    * handler 内同步 TdxClient 调用经 ``asyncio.to_thread`` 执行（V4）；
    * ``config.max_message`` → ``serve(max_size=...)``；
      ``config.path`` → 握手期路径校验，不匹配 403 拒绝升级（V7）；
    * 后台推送任务按 ``config.push_interval`` 轮询已订阅符号并推送
      ``quote_update`` 通知（附着在返回 Server 的 ``_tstdx_push_task``）。

    用法::

        server = await serve_ws(config=WsConfig())
        await server.serve_forever()
    """
    try:
        ws_serve, modern = _import_ws_serve()
    except ImportError as exc:
        raise RuntimeError("WebSocket 服务需要: pip install tstdx[server]") from exc

    template = handler or JsonRpcHandler()
    # 深审 L11：模板无 client 时**此处**惰性创建唯一 TdxClient——若留给各
    # 连接 handler 的 _client_obj 惰性创建，每个 WS 连接会各自新建客户端
    # （各自独立连接池 + 限流器），N 连接 = N 倍资源放大且无共享配额。
    if template._client is None:
        from ..client import TdxClient

        template._client = TdxClient()
    # 同理：门面（复权/全市场/板块/分钟K线/F10）也共享单实例，避免 N 连接
    # N 份路由熔断状态与连接管理。
    if template._facade is None:
        from ..facade.api import UnifiedQuoteAPI

        template._facade = UnifiedQuoteAPI()
    cfg = config or WsConfig()

    connections: dict[Any, JsonRpcHandler] = {}

    def _modern_process_request(ws: Any, request: Any) -> Any:
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        if request.path != cfg.path:
            return Response(403, "Forbidden", Headers(), b"forbidden: invalid path\n")
        return None

    def _legacy_process_request(path: str, request_headers: Any) -> Any:
        if path != cfg.path:
            return (403, [("Content-Type", "text/plain")], b"forbidden: invalid path\n")
        return None

    process_request: Any = _modern_process_request if modern else _legacy_process_request

    async def _connection(ws: Any, _path: str | None = None) -> None:
        # 每连接独立 handler：订阅集互不污染；共享模板的底层客户端与门面
        conn_handler = JsonRpcHandler(client=template._client, facade=template._facade)
        connections[ws] = conn_handler
        try:
            async for raw in ws:
                # V4：同步 TdxClient 调用放线程池，避免卡死事件循环
                reply, added = await asyncio.to_thread(conn_handler.on_message, raw)
                if reply is not None:
                    await ws.send(reply)
                if added:
                    # F3 断线补洞：新增订阅（含重连后重订阅）立即推全量快照，
                    # 客户端无需等下一轮 quote_update 即可对齐漏推区间
                    notes = await asyncio.to_thread(conn_handler.push_snapshot, added)
                    for note in notes:
                        await ws.send(note)
        finally:
            connections.pop(ws, None)

    async def _push_loop() -> None:
        interval = float(cfg.push_interval) if cfg.push_interval > 0 else 3.0
        while True:
            await asyncio.sleep(interval)
            for ws, conn_handler in list(connections.items()):
                try:
                    syms = conn_handler.subscription_snapshot()
                    if not syms:
                        continue
                    notes = await asyncio.to_thread(conn_handler.push_quote_updates, syms)
                    for note in notes:
                        await ws.send(note)
                except Exception as exc:  # noqa: BLE001 —— 单连接推送失败不杀循环
                    logger.debug("ws push loop: send failed: %s", exc)

    async def _main() -> Any:
        push_task = asyncio.create_task(_push_loop())
        try:
            server = await ws_serve(
                _connection,
                cfg.host,
                cfg.port,
                max_size=cfg.max_message,
                process_request=process_request,
            )
        except BaseException:
            push_task.cancel()
            raise
        server._tstdx_push_task = push_task  # noqa: SLF001 —— 供调用方优雅关闭
        return server

    return _main()


def main() -> None:  # pragma: no cover —— CLI 入口
    """阻塞式入口：asyncio.run 驱动 serve_forever，KeyboardInterrupt 优雅收尾。"""

    async def _run() -> None:
        server = await serve_ws()
        push_task = getattr(server, "_tstdx_push_task", None)
        try:
            await server.serve_forever()
        finally:
            # 同步清理：close + 推送任务取消；剩余回收交给 asyncio.run 收尾
            server.close()
            if push_task is not None:
                push_task.cancel()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("ws_server: interrupted, exiting.")
