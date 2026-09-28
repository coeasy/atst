# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical v13 JSON-RPC handler.

The handler owns two surfaces:

* **request/response** JSON-RPC (``quotes`` / ``bars`` / ``snapshot`` / ``minute`` /
  ``trades`` / ``security.*`` / ``query`` / ``runtime.*``) — every call is delegated
  to :class:`~atst.client.api.Client` and translated into one of its semantic methods.
* **streaming control** (``subscribe`` / ``unsubscribe`` / ``list``) — these bridge the
  in-process :class:`~atst.streaming.StatefulQuoteStream` onto the WebSocket connection so
  a client receives server-pushed ``snapshot`` / ``tick`` / ``error`` frames instead of
  polling. The push frames are emitted from the streaming worker through the connection's
  ``send`` callback; subscription state is **per connection** (the server builds one
  handler instance per connection), so two clients never share each other's streams.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from ..client.api import AsyncClient, Client
from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..providers import PROVIDERS
from ..runtime.orchestration import FallbackPolicy
from .serialization import serialize_result
from .wire_fields import (
    WS_PARAMS_FIELDS,
    as_request_int,
    reject_reserved_kwargs,
    reject_undeclared,
)

__all__ = ["RuntimeJsonRpcHandler"]

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

#: 推送帧的方法名（与请求/响应方法区分，避免和 ``subscribe`` 等控制方法混淆）。
PUSH_METHOD = "push"


class RuntimeJsonRpcHandler:
    METHODS = frozenset(
        {
            "quotes",
            "bars",
            "snapshot",
            "minute",
            "trades",
            "security.count",
            "security.list",
            "query",
            "runtime.capabilities",
            "runtime.health",
            # 流式控制面：把进程内实时流桥接到这条连接上。
            "subscribe",
            "unsubscribe",
            "list",
        }
    )

    def __init__(self, client: Client | None = None) -> None:
        self.client = client or Client()
        #: 传入的 client 归调用方，自己造的才由 :meth:`close` 释放——与 MCP 面
        #: ``MCPServer._owns_client``（``_server.py``）同一口径。
        self._owns_client = client is None
        #: 每个连接一份：流式控制面需要把推送帧送到具体连接，而 ``serve_runtime_ws``
        #: 在调用方不传 handler 时为每条连接造一份 handler，所以这里的订阅表天然
        #: 是连接私有的，不会跨连接串台。
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send: Any | None = None
        self._async_client: AsyncClient | None = None
        self._subs: dict[str, Any] = {}
        self._seq = 0

    # ------------------------------------------------------------------
    # 连接生命周期（由 ``serve_runtime_ws`` 的每条连接调用一次）
    # ------------------------------------------------------------------

    def bind_connection(self, loop: asyncio.AbstractEventLoop, send: Any) -> None:
        """把这条连接的事件循环与发送口登记进来，流式推送才能落到正确的 socket。

        必须在连接建立后、第一条消息之前调用；调用方传进来的 ``send`` 应是
        ``websocket.send`` 这类可等待协程函数。
        """
        self._loop = loop
        self._send = send
        self._async_client = AsyncClient(client=self.client)

    async def stop_all_subscriptions(self) -> None:
        """停掉本连接拥有的全部实时流，幂等。连接关闭或 handler 释放时调用。

        每个流都带 ``timeout`` 上界（见 :class:`~atst.streaming.StatefulQuoteStream`），
        因此这里不会无限挂起——连接断开后最多等到流的排空超时即返回。
        """
        streams = list(self._subs.values())
        self._subs.clear()
        for stream in streams:
            with contextlib.suppress(Exception):
                await stream.stop()

    def close(self) -> None:
        """释放本处理器自建的 ``Client``，并尽量停掉残留的实时流。幂等。

        过去 WS 面没有任何释放入口（第 26 轮 F-100）。这条链今天不释放 socket 与心跳
        线程——``Client`` 跨调用不持有连接，内置执行器也没有 ``close()``（本轮实测，判据
        ``tests/runtime/test_close_chain_ownership.py``）；它兑现的是"自造的才由自造者关"
        这条所有权口径，以及注入执行器那条形同协议的口子。
        """
        if self._loop is not None and self._subs:
            # 尽力而为：连接侧通常已经 stop_all_subscriptions，这里只是兜底。
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(
                    self.stop_all_subscriptions(), self._loop
                ).result(timeout=5)
        if self._owns_client:
            self._owns_client = False
            self.client.close()

    # ------------------------------------------------------------------
    # JSON-RPC 入口（同步；由服务器用 ``asyncio.to_thread`` 调度）
    # ------------------------------------------------------------------

    def handle_message(self, raw: str | bytes) -> str | None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._protocol_error(None, ERR_PARSE, "parse error")
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            return self._protocol_error(
                message.get("id") if isinstance(message, dict) else None,
                ERR_INVALID_REQUEST,
                "invalid request",
            )
        request_id = message.get("id")
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        if request_id is None:
            with contextlib.suppress(Exception):
                self._dispatch(method, params if isinstance(params, dict) else {})
            return None
        if method not in self.METHODS:
            return self._protocol_error(request_id, ERR_METHOD_NOT_FOUND, "method not found")
        if not isinstance(params, dict):
            return json.dumps(
                self._error(
                    request_id,
                    ERR_INVALID_PARAMS,
                    "invalid params",
                    ValidationError("params must be an object"),
                ),
                ensure_ascii=False,
            )
        try:
            result = self._dispatch(method, params)
        except ValidationError as exc:
            return json.dumps(
                self._error(request_id, ERR_INVALID_PARAMS, "invalid params", exc),
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                self._error(request_id, ERR_INTERNAL, "request failed", exc), ensure_ascii=False
            )
        return json.dumps(
            {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result},
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _policy(params: dict[str, Any]) -> FallbackPolicy | None:
        return FallbackPolicy.from_wire(params.get("fallback"))

    @staticmethod
    def _int_param(method: str, name: str, value: Any, default: int) -> int:
        """整数格走 :func:`as_request_int`：坏值当场 E1010，不再裸 ``int()`` 炸成 E9000。

        形参收**已取出的值**而不是整个 ``params``：调用处必须自己写 ``params.get("count")``，
        ``tests/runtime/test_wire_declared_fields.py`` 那份 AST 才看得见"声明的字段有人读"。
        键名一旦变成变量，那道判据就当场失明（第 25 轮建口时被它抓到一次）。
        """
        return as_request_int(
            face="ws_params",
            where=f"WS {method} params",
            name=name,
            value=value,
            default=default,
        )

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        # params 的白名单按方法给出（``wire_fields.WS_PARAMS_FIELDS``）：过去未知键经
        # ``params.get(...)`` 蒸发，同一个调用在 ``Client`` 面上会 ``TypeError``（F-47）。
        reject_undeclared(
            face="ws_params",
            where=f"WS {method} params",
            declared=WS_PARAMS_FIELDS[method],
            received=params.keys(),
        )
        if method == "runtime.health":
            core = Client.core_capability_statuses()
            return {
                "status": "ok",
                "api": "v13",
                "default_provider": self.client.runtime.planner.default_provider,
                "migrated_capabilities": len(self.client.capabilities()),
                "core_unavailable": sorted(
                    capability for capability, state in core.items() if not state["available"]
                ),
            }
        if method == "runtime.capabilities":
            return {
                "capabilities": list(self.client.capabilities()),
                "providers": {
                    provider: {
                        channel.id: sorted(channel.capabilities)
                        for channel in PROVIDERS.get(provider).channels
                    }
                    for provider in PROVIDERS.ids()
                },
            }
        if method == "query":
            capability = params.get("capability")
            if not isinstance(capability, str) or not capability.strip():
                raise ValidationError("capability is required")
            args = params.get("args", [])
            kwargs = params.get("kwargs", {})
            if not isinstance(args, list):
                raise ValidationError("args must be an array")
            if not isinstance(kwargs, dict):
                raise ValidationError("kwargs must be an object")
            reject_reserved_kwargs(
                face="ws_params",
                where='WS method "query" 的 kwargs',
                kwargs=kwargs,
            )
            return serialize_result(
                self.client.call(
                    capability,
                    *args,
                    provider=params.get("provider"),
                    channel=params.get("channel"),
                    currentness=str(params.get("currentness", "business")),
                    **kwargs,
                )
            )

        if method == "subscribe":
            # 这些 ``params.get`` 必须留在 ``_dispatch`` 体内：``test_wire_declared_fields``
            # 用 AST 扫的是 ``_dispatch`` + ``_policy`` 的读取点，迁到 helper 会被判成
            # "声明了却无人读"（wire 面版的 max_age）。
            return self._subscribe_from_params(
                symbols=params.get("symbols"),
                provider=params.get("provider"),
                interval=params.get("interval"),
                diff_only=params.get("diff_only"),
                max_queue=params.get("max_queue"),
            )
        if method == "unsubscribe":
            return self._unsubscribe_from_params(params.get("id"))
        if method == "list":
            return self._list_subscriptions()

        provider = params.get("provider")
        if method == "quotes":
            symbols = params.get("symbols")
            if not isinstance(symbols, (str, list, tuple)) or not symbols:
                raise ValidationError("symbols is required")
            return serialize_result(
                self.client.quotes(
                    symbols,
                    provider=provider,
                    policy=self._policy(params),
                    currentness="live",
                )
            )
        if method == "bars":
            symbol = params.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            return serialize_result(
                self.client.bars(
                    symbol,
                    provider=provider,
                    policy=self._policy(params),
                    period=str(params.get("period", "day")),
                    count=self._int_param(method, "count", params.get("count"), 320),
                    start=self._int_param(method, "start", params.get("start"), 0),
                    adjustment=str(params.get("adjustment", "")),
                    currentness="historical",
                )
            )

        symbol = params.get("symbol")
        if method in {"snapshot", "minute", "trades"}:
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            if method == "snapshot":
                return serialize_result(self.client.snapshot(symbol, provider=str(provider or "tdx")))
            if method == "minute":
                return serialize_result(self.client.minute(symbol, provider=provider))
            return serialize_result(
                self.client.trades(
                    symbol,
                    provider=provider,
                    start=self._int_param(method, "start", params.get("start"), 0),
                    count=self._int_param(method, "count", params.get("count"), 0),
                )
            )

        chosen = str(provider or "tdx")
        market = params.get("market", 0)
        if method == "security.count":
            return serialize_result(self.client.security_count(market=market, provider=chosen))
        if method == "security.list":
            return serialize_result(
                self.client.security_list(
                    market=market,
                    start=self._int_param(method, "start", params.get("start"), 0),
                    provider=chosen,
                )
            )
        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------
    # 流式控制面
    # ------------------------------------------------------------------

    def _subscribe_from_params(
        self,
        *,
        symbols: Any,
        provider: Any,
        interval: Any,
        diff_only: Any,
        max_queue: Any,
    ) -> dict[str, Any]:
        """``subscribe``：校验入参、起一个实时流、回 ack。

        没有绑定连接（纯单测里的 handler）时只回 ack、不起流——真实连接一定先
        ``bind_connection``，所以推送只发生在有 socket 的会话里。
        """
        if not isinstance(symbols, (str, list, tuple)) or not symbols:
            raise ValidationError("symbols is required")
        if isinstance(symbols, str):
            symbols = [symbols]
        interval = float(self._int_param("subscribe", "interval", interval, 1) or 1)
        diff_only = bool(diff_only) if diff_only is not None else False
        max_queue = int(self._int_param("subscribe", "max_queue", max_queue, 1024) or 1024)
        if self._loop is None or self._send is None:
            # 无连接的上下文（门禁/单测）：只声明订阅 id，不起流。
            self._seq += 1
            return {"subscription_id": f"sub{self._seq}", "status": "subscribed"}
        sub_id = asyncio.run_coroutine_threadsafe(
            self._subscribe(list(symbols), provider, interval, diff_only, max_queue),
            self._loop,
        ).result(timeout=10)
        return {"subscription_id": sub_id, "status": "subscribed"}

    async def _subscribe(
        self, symbols: list[str], provider: Any, interval: float, diff_only: bool, max_queue: int
    ) -> str:
        if self._async_client is None:  # 防御：理论上 bind_connection 已建立
            self._async_client = AsyncClient(client=self.client)
        stream = self._async_client.stream(
            symbols,
            provider=provider,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=self._on_quote,
            on_error=self._on_error,
        )
        await stream.start()
        self._seq += 1
        sub_id = f"sub{self._seq}"
        self._subs[sub_id] = stream
        return sub_id

    def _unsubscribe_from_params(self, sub_id: Any) -> dict[str, Any]:
        if not isinstance(sub_id, str) or not sub_id:
            raise ValidationError("id is required")
        found = sub_id in self._subs
        if found:
            stream = self._subs.pop(sub_id)
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(
                    self._safe_stop(stream), self._loop
                ).result(timeout=10)
        return {"status": "unsubscribed", "id": sub_id, "found": found}

    async def _safe_stop(self, stream: Any) -> None:
        with contextlib.suppress(Exception):
            await stream.stop()

    def _list_subscriptions(self) -> dict[str, Any]:
        items = []
        for sub_id, stream in self._subs.items():
            # 真实 ``AsyncStatefulQuoteStream.state`` 是 ``StreamState`` 枚举（有 ``.value``），
            # 但流式实现可能演进为其它形状（含裸字符串）。这里对两种形状都稳健：
            # 枚举取 ``.value``，裸字符串/缺属性回退到原值或 ``"running"``，绝不因状态
            # 形状差异把 ``list`` 这种只读调用吞成 E9000（第 31 轮判据暴露的脆弱点）。
            state = getattr(stream, "state", None)
            if state is None:
                state_value: Any = "running"
            else:
                state_value = getattr(state, "value", state)
            items.append({"id": sub_id, "state": state_value})
        return {"subscriptions": items}

    def _on_quote(self, symbol: str, data: dict[str, Any]) -> None:
        """流式 worker 每轮轮询到快照时调用（同步回调）。

        推送帧通过 ``run_coroutine_threadsafe`` 调度到连接循环，不阻塞流式 worker；
        单条 ``send`` 失败只记日志，不影响其它订阅。
        """
        if self._loop is None or self._send is None:
            return
        frame = {
            "jsonrpc": JSONRPC_VERSION,
            "method": PUSH_METHOD,
            "params": {"type": "snapshot", "code": symbol, "data": data},
        }
        try:
            asyncio.run_coroutine_threadsafe(self._emit(frame), self._loop)
        except Exception:  # pragma: no cover - 连接已断等情况
            logger.exception("WS 推送帧调度失败")

    def _on_error(self, exc: Exception) -> None:
        if self._loop is None or self._send is None:
            return
        envelope = to_error_envelope(exc)
        # 错误码在 ``envelope.to_dict()["code"]`` 里，顶层不再放一个无意义的 ``None``。
        frame = {
            "jsonrpc": JSONRPC_VERSION,
            "method": PUSH_METHOD,
            "params": {"type": "error", "error": envelope.to_dict()},
        }
        try:
            asyncio.run_coroutine_threadsafe(self._emit(frame), self._loop)
        except Exception:  # pragma: no cover
            logger.exception("WS 错误帧调度失败")

    async def _emit(self, frame: dict[str, Any]) -> None:
        if self._send is None:
            return
        await self._send(json.dumps(frame, ensure_ascii=False, default=str))

    # ------------------------------------------------------------------
    # 协议层错误
    # ------------------------------------------------------------------

    @staticmethod
    def _protocol_error(request_id: Any, rpc_code: int, message: str) -> str:
        """协议层失败（解析 / 请求形状 / 方法派发）：同样挂上规范化信封。

        与 MCP 面 ``MCPServer._protocol_error`` 同口径。第 30 轮修前，WS 面的协议层失败
        只回 ``{"code", "message"}``、没有 ``error.data``——``_error`` 自己的 docstring 与
        ``docs/errors.md`` §四 都写着"两面都把信封挂在 JSON-RPC ``error.data``"，而一个
        照文档读 ``data.phase`` / ``data.request_id`` 的客户端，恰好在"方法名写错"这种最
        该自查的一格上读到 ``None``。``message`` 也与人读理由分开：这里的 ``message`` 是
        通用 RPC 文案，信封里的 ``message`` 才是这条失败的具体理由（与业务失败一致）。
        """

        return json.dumps(
            RuntimeJsonRpcHandler._error(
                request_id,
                rpc_code,
                message,
                ValidationError(
                    message,
                    context={
                        "phase": "ws_protocol",
                        # 与 MCP 面 ``_protocol_error`` 同一组 legacy 许可字段：两面协议层
                        # 失败的 ``error.data`` 逐格同形，客户端读法不必按面分叉。
                        "fallback": False,
                        "provider_switch_allowed": False,
                    },
                ),
            ),
            ensure_ascii=False,
        )

    @staticmethod
    def _error(
        request_id: Any, rpc_code: int, message: str, exc: Exception | None = None
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": rpc_code, "message": message}
        if exc is not None:
            # Attach the envelope with the request identity so a WS client can
            # correlate the failure the same way HTTP/MCP/CLI clients do.
            error["data"] = to_error_envelope(
                exc,
                request_id=None if request_id is None else str(request_id),
            ).to_dict()
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}
