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
  ``send`` callback; subscription bookkeeping (which ``sub_id`` exists on which connection)
  is **per connection** (the server builds one handler instance per connection), so two
  clients never see each other's ``list`` / ``unsubscribe``.

  What *is* shared is the upstream poll loop (F-8): connections watching the same symbols
  through the same provider attach to one :class:`_SharedPoller` instead of each starting
  its own loop. The poller owns the stream and fans each frame out to every subscriber's
  own ``send``; delivery stays per-connection, and a dead connection only drops itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from typing import Any

from ..client.api import AsyncClient, Client
from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..providers import PROVIDERS
from ..runtime.executor import DIRECT_BINDINGS
from ..runtime.orchestration import FallbackPolicy
from .serialization import serialize_result
from .wire_fields import (
    WS_PARAMS_FIELDS,
    as_request_bool,
    as_request_float,
    as_request_int,
    parse_query_call,
    reject_undeclared,
)

__all__ = ["RuntimeJsonRpcHandler"]

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"

#: 单条 WS 连接上允许"已投递但未发完"的推送帧上限。见 :meth:`_schedule_push`：
#: 慢客户端反压时丢旧帧，而不是让待发协程与内存无界上涨。
MAX_PENDING_PUSH_FRAMES: int = 64

#: 单条连接上"标的 → Provider"出处表的容量上限（FIFO 淘汰）。
MAX_TRACKED_SUB_PROVIDERS: int = 1024
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

#: 推送帧的方法名（与请求/响应方法区分，避免和 ``subscribe`` 等控制方法混淆）。
PUSH_METHOD = "push"

#: F-8：等价订阅共享同一条上游轮询的登记表。
#:
#: 键里带上 ``loop`` / ``Client`` 的 ``id``：两条连接只有在同一事件循环、同一份
#: ``Client``（即同一套 runtime 配置）上才可能共享上游——跨 server / 跨循环的订阅
#: 即使标的相同也各起一条，绝不把一条循环里的流借给另一条循环。
#: 值在引用计数归零时摘除（见 :meth:`_SharedPoller.detach`），进程里不留条目。
_POLLERS: dict[tuple[Any, ...], _SharedPoller] = {}
_POLLERS_LOCK = threading.RLock()


def _poller_key(
    loop: asyncio.AbstractEventLoop,
    client: Any,
    symbols: Any,
    provider: str,
    interval: float,
    diff_only: bool,
    max_queue: int,
) -> tuple[Any, ...]:
    """共享键：``(loop, client, 排序后的标的, provider, 节奏参数)``。

    节奏参数（``interval`` / ``diff_only`` / ``max_queue``）也进键，是刻意的：把
    ``interval=5`` 的订阅挂到 ``interval=1`` 的轮询上会静默改掉它的节拍，那属于行为
    变更而不是去重。同参数重复订阅（F-8 说的那个场景）才共享。
    """
    return (
        id(loop),
        id(client),
        tuple(sorted(str(symbol) for symbol in symbols)),
        provider,
        float(interval),
        bool(diff_only),
        int(max_queue),
    )


class _SharedPoller:
    """一条上游轮询，被所有等价订阅共享（F-8）。

    流只建一次，回调指向本对象而不是某条连接——因此 poller 不持有连接对象，连接
    断开不会把流带走；第二条连接订阅同一批标的只是往 ``subscribers`` 里加一格。
    每帧逐条 fan-out：单条连接的投递失败只摘它自己（:meth:`drop`），同一 poller 上
    其它连接的推送不受影响。
    """

    def __init__(
        self,
        key: tuple[Any, ...],
        *,
        client: Any,
        loop: asyncio.AbstractEventLoop,
        symbols: list[str],
        provider: str,
        interval: float,
        diff_only: bool,
        max_queue: int,
    ) -> None:
        self.key = key
        #: 强引用 ``loop`` / ``client``：键里用的是它们的 ``id``，登记项还在时这两个
        #: 对象就不会被回收，``id`` 也不会被新对象复用，不会把两条无关订阅撞进一格。
        self.client = client
        self.loop = loop
        self.symbols = list(symbols)
        self.provider = provider
        self.interval = interval
        self.diff_only = diff_only
        self.max_queue = max_queue
        self.stream: Any | None = None
        #: ``(id(handler), sub_id)`` → ``(handler, sub_id)``。``sub_id`` 只在单条连接
        #: 内唯一（``sub1`` / ``sub2``…），所以 handler 身份必须进键。
        self.subscribers: dict[tuple[int, str], tuple[Any, str]] = {}
        #: 最近一帧原始 payload（按标的）：后加入的连接 attach 时回放一次，免得
        #: "第二条连接要等下一个轮询节拍才看到数据"——单订阅者的首帧语义因此保持。
        self.last_data: dict[str, dict[str, Any]] = {}
        #: 引用计数归零后置真；此后任何 attach 都不会再挂上来（fail-closed）。
        self.closed = False

    @property
    def refcount(self) -> int:
        with _POLLERS_LOCK:
            return len(self.subscribers)

    @property
    def state(self) -> Any:
        """给 ``list`` 用的流状态（与真实流同形状：枚举或裸字符串）。"""
        stream = self.stream
        return None if stream is None else getattr(stream, "state", None)

    def attach(self, handler: Any, sub_id: str) -> bool:
        """挂一个订阅者；poller 已收尾时返回 ``False``（调用方重建）。"""
        with _POLLERS_LOCK:
            if self.closed:
                return False
            self.subscribers[(id(handler), sub_id)] = (handler, sub_id)
            return True

    def detach(self, handler: Any, sub_id: str) -> Any | None:
        """摘一个订阅者；返回"该停的流"仅当这是最后一个订阅者。

        摘除与登记表删除在同一把锁内完成，因此引用计数归零后不会再有 attach 进来
        （fail-closed）；返回的流由调用方 ``await stream.stop()`` 排空。
        """
        with _POLLERS_LOCK:
            self.subscribers.pop((id(handler), sub_id), None)
            if self.subscribers:
                return None
            self.closed = True
            if _POLLERS.get(self.key) is self:
                del _POLLERS[self.key]
            return self.stream

    def fan_out(self, symbol: str, data: dict[str, Any]) -> None:
        """流式 worker 的回调：把一帧分发给每个订阅者，各自独立失败。"""
        if isinstance(data, dict):
            with _POLLERS_LOCK:
                self.last_data[str(symbol)] = data
        with _POLLERS_LOCK:
            targets = list(self.subscribers.values())
        for handler, sub_id in targets:
            try:
                handler._on_quote(symbol, data)
            except Exception:  # pragma: no cover - 防御：回调本身不应抛
                logger.exception("WS 订阅投递失败，摘除该订阅")
                self.drop(handler, sub_id)

    def fan_out_error(self, exc: Exception) -> None:
        with _POLLERS_LOCK:
            targets = list(self.subscribers.values())
        for handler, sub_id in targets:
            try:
                handler._on_error(exc)
            except Exception:  # pragma: no cover - 防御：回调本身不应抛
                logger.exception("WS 错误帧投递失败，摘除该订阅")
                self.drop(handler, sub_id)

    def replay(self, handler: Any) -> None:
        """新订阅者接入已在跑的 poller：先补一帧当前快照（没有就什么都不做）。"""
        with _POLLERS_LOCK:
            pending = [
                (symbol, self.last_data[str(symbol)])
                for symbol in self.symbols
                if str(symbol) in self.last_data
            ]
        for symbol, data in pending:
            with contextlib.suppress(Exception):
                handler._on_quote(symbol, data)

    def drop(self, handler: Any, sub_id: str) -> None:
        """摘掉一条投递失败的订阅（只影响这一条连接上的这一格）。"""
        stream = handler._forget_subscription(sub_id)
        if stream is not None:
            handler._spawn(handler._safe_stop(stream))


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
        #: 是连接私有的，不会跨连接串台。表里存的是 :class:`_SharedPoller`（F-8 后
        #: 多条连接可以共享同一条上游轮询，但"谁订了什么"仍按连接记账）。
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send: Any | None = None
        self._async_client: AsyncClient | None = None
        self._subs: dict[str, Any] = {}
        self._seq = 0
        #: P1-9/M5：订阅表（``_subs`` / ``_sub_providers`` / ``_sub_symbols``）的访问
        #: 统一纪律——变更与快照都过这把锁，且订阅/unsubscribe 变更经
        #: ``run_coroutine_threadsafe`` 调度到 loop 线程执行。过去 ``handle_message``
        #: 经 ``asyncio.to_thread`` 调度，unsubscribe/list 在 worker 线程直接读写这批
        #: dict，而 ``_subscribe`` / ``_on_quote`` 在 loop 线程与流式 worker 线程碰同一批
        #: 键，无锁并发下 list 迭代可能撞上并发变更（RuntimeError → E9000）。
        self._subs_lock = threading.RLock()
        #: 已投出去、还没跑完的推送帧（:meth:`_schedule_push` 的背压窗口）。
        self._pending_pushes: list[Any] = []
        #: 标的 → 订阅时选中的 Provider（推送帧的 ``meta.provider`` 出处）。
        self._sub_providers: dict[str, str] = {}
        #: 订阅 id → 标的表（``list`` 面向消费方的对账口径，``_subscribe`` 登记）。
        self._sub_symbols: dict[str, list[str]] = {}
        #: 本连接的发送口已经坏过一次：只摘一次订阅，不重复排空。
        self._delivery_broken = False

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

        F-8 之后"停本连接的订阅"与"停上游轮询"不再等价：只有本连接是最后一个
        订阅者时才由这里 ``stop`` 那条流；还有别的连接在听时，流继续跑，本连接
        只是从 fan-out 名单里摘掉。
        """
        with self._subs_lock:
            pollers = list(self._subs.items())
            self._subs.clear()
            self._sub_symbols.clear()
            #: 没有活跃订阅时整表清空（与 ``_unsubscribe_locked`` 同一口径）。
            self._sub_providers.clear()
        streams: list[Any] = []
        for sub_id, poller in pollers:
            stream = poller.detach(self, sub_id)
            if stream is not None:
                streams.append(stream)
        for stream in streams:
            with contextlib.suppress(Exception):
                await stream.stop()

    async def close(self) -> None:
        """释放本处理器自建的 ``Client``，并停掉残留的实时流。幂等。

        P1-9/M4：改成协程、在 loop 线程内直接 ``await`` 收尾。旧实现是同步方法里
        ``run_coroutine_threadsafe(...).result(timeout=5)``，而 ``__main__`` 宿主在
        loop 线程的 ``finally`` 里调它——协程永远得不到调度，白等 5 秒后 TimeoutError
        被 suppress，残留流并未被停。现在宿主 ``await handler.close()``，同一循环内
        直接排空订阅；跨线程的调用方也可以 ``asyncio.run(handler.close())``。

        这条链今天不释放 socket 与心跳线程——``Client`` 跨调用不持有连接，内置执行器
        也没有 ``close()``（本轮实测，判据 ``tests/runtime/test_close_chain_ownership.py``）；
        它兑现的是"自造的才由自造者关"这条所有权口径，以及注入执行器那条形同协议的口子。
        """
        if self._loop is not None and self._subs:
            # 同一 loop 内直接排空：stop_all_subscriptions 里每个 stream.stop 都有
            # 有界排空（见其 docstring），不会把宿主停机挂在无界等待上。
            with contextlib.suppress(Exception):
                await self.stop_all_subscriptions()
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
            # P2-6/L4：业务失败的顶层 message 提为信封 message（与 MCP/HTTP 对齐——
            # 过去顶层是通用文案 "request failed"，细节只在 ``error.data.message``，
            # 同一失败三面人读位置不同）。协议层失败（parse/invalid request/方法不存在
            # /invalid params）仍是通用 RPC 文案，理由在信封。
            envelope = to_error_envelope(exc, request_id=str(request_id))
            return json.dumps(
                self._error(request_id, ERR_INTERNAL, envelope.message, exc), ensure_ascii=False
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

    @staticmethod
    def _bool_param(method: str, name: str, value: Any, default: bool = False) -> bool:
        """布尔格走 :func:`as_request_bool`：``"false"`` 不再被真值规则读成 ``True``。"""
        return as_request_bool(
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
            core = self.client.core_capability_statuses()
            return {
                "status": "ok",
                "api": "v13",
                "default_provider": self.client.runtime.planner.default_provider,
                #: 与 HTTP 面 ``/v13/runtime/health`` 同一读数：执行绑定数是
                #: 运维判断「这条部署面铺开了多少直连绑定」的口径，两面必须一致。
                "direct_bindings": len(DIRECT_BINDINGS),
                "migrated_capabilities": len(self.client.capabilities()),
                "core_unavailable": sorted(
                    capability
                    for capability, state in core.items()
                    if not state["default_available"]
                ),
                #: 与 HTTP 面 ``/v13/runtime/health`` 同一读数（同一份名单、同一个 TTL）。
                "provider_health": self.client.provider_health_status(),
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
            # 这些 ``params.get`` 必须留在 ``_dispatch`` 体内（``test_wire_declared_fields``
            # 的 AST 判据只扫这里）；形状校验本体收口到 :func:`parse_query_call`（P2-6/L1）。
            capability, args, kwargs = parse_query_call(
                face="ws_params",
                where='WS method "query" 的 kwargs',
                capability=params.get("capability"),
                args=params.get("args", []),
                kwargs=params.get("kwargs", {}),
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
                    strict=self._bool_param(method, "strict", params.get("strict", False)),
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
                    currentness=str(params.get("currentness", "historical")),
                    strict=self._bool_param(method, "strict", params.get("strict", False)),
                    start_date=str(params.get("start_date", "")),
                    end_date=str(params.get("end_date", "")),
                )
            )
        symbol = params.get("symbol")
        if method in {"snapshot", "minute", "trades"}:
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            if method == "snapshot":
                return serialize_result(
                    self.client.snapshot(symbol, provider=str(provider or "tdx"))
                )
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
        interval = as_request_float(
            face="ws_params",
            where="WS subscribe params",
            name="interval",
            value=interval,
            default=1.0,
            lo=0.0,
        )
        diff_only = self._bool_param("subscribe", "diff_only", diff_only, False)
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
        provider_key = str(provider) if provider else "tdx"
        with self._subs_lock:
            self._seq += 1
            sub_id = f"sub{self._seq}"
        key = _poller_key(
            self._loop,  # type: ignore[arg-type]
            self.client,
            symbols,
            provider_key,
            interval,
            diff_only,
            max_queue,
        )
        poller, created = self._acquire_poller(
            key, symbols, provider_key, interval, diff_only, max_queue, sub_id
        )
        with self._subs_lock:
            self._subs[sub_id] = poller
            #: 记住这条订阅用的 Provider：推送帧要把出处带上，否则消费方分不清这一帧
            #: 是 TDX 还是别家（请求-响应路径的 ``meta.provider`` 是同一件事）。
            for symbol in symbols:
                self._sub_providers[str(symbol)] = provider_key
            #: 订阅 → 标的表：``list`` 要把每条订阅订了什么暴露给消费方，否则客户端
            #: 没法把标的对应到 ``sub_id``（退订/对账都无从下手）。
            self._sub_symbols[sub_id] = list(symbols)
        if created:
            # ``_acquire_poller`` 只在 created=True 时返回新建的 poller，且建时已把
            # ``stream`` 赋好；这里显式取局部量只为让类型收窄（mypy）。
            new_stream = poller.stream
            assert new_stream is not None
            try:
                await new_stream.start()
            except BaseException:
                # 起流失败：这条订阅不能留在表里，否则退订/关连接会去停一条半死的流。
                stream = self._forget_subscription(sub_id)
                if stream is not None:
                    await self._safe_stop(stream)
                raise
        else:
            #: 后加入的连接拿一帧当前快照，与"新起一条流时的首帧"对齐；没有快照就
            #: 等下一个轮询节拍，不伪造数据。
            poller.replay(self)
        return sub_id

    def _acquire_poller(
        self,
        key: tuple[Any, ...],
        symbols: list[str],
        provider: str,
        interval: float,
        diff_only: bool,
        max_queue: int,
        sub_id: str,
    ) -> tuple[_SharedPoller, bool]:
        """取（或建）同键的共享轮询，并在同一把锁里把本订阅挂上去。

        "建流 + 登记 + 挂订阅者"必须原子：否则两个并发 subscribe 会各建一条流，
        F-8 要修的重复轮询就还在。``closed`` 的登记项在 :meth:`_SharedPoller.detach`
        里已被同一把锁摘掉，所以这里拿到的一定是活的；``attach`` 的返回值只是
        fail-closed 兜底。
        """
        with _POLLERS_LOCK:
            poller = _POLLERS.get(key)
            if poller is not None and poller.attach(self, sub_id):
                return poller, False
            poller = _SharedPoller(
                key,
                client=self.client,
                loop=self._loop,  # type: ignore[arg-type]
                symbols=symbols,
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
            )
            #: 回调指向 poller 自己，而不是某条连接的 ``_on_quote``——流因此不持有
            #: 任何连接对象，跨连接共享才是安全的。
            async_client = self._async_client
            assert async_client is not None  # _subscribe_from_params 已建好
            poller.stream = async_client.stream(
                list(symbols),
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
                on_quote=poller.fan_out,
                on_error=poller.fan_out_error,
            )
            _POLLERS[key] = poller
            poller.attach(self, sub_id)
            return poller, True

    def _unsubscribe_from_params(self, sub_id: Any) -> dict[str, Any]:
        if not isinstance(sub_id, str) or not sub_id:
            raise ValidationError("id is required")
        # P1-9/M5：订阅表变更统一到 loop 线程——这里只做入参校验，读写与停流都在
        # loop 上执行（与 ``_subscribe_from_params`` 同一调度形状），不再在 worker
        # 线程直接碰 ``_subs``。无连接上下文（门禁/单测）没有 loop 可调度，就地清理
        # （与旧实现同口径：无 loop 时只摘表，不停流——没有连接就没有可停的 socket）。
        if self._loop is None:
            result = self._unsubscribe_locked(sub_id)
            result.pop("_stream", None)
            return result
        return asyncio.run_coroutine_threadsafe(self._unsubscribe(sub_id), self._loop).result(
            timeout=10
        )

    async def _unsubscribe(self, sub_id: str) -> dict[str, Any]:
        """loop 线程上的退订：摘表 + 只在最后一个订阅者时停流。"""
        result = self._unsubscribe_locked(sub_id)
        stream = result.pop("_stream", None)
        if stream is not None:
            await self._safe_stop(stream)
        return result

    def _unsubscribe_locked(self, sub_id: str) -> dict[str, Any]:
        with self._subs_lock:
            found = sub_id in self._subs
        #: 引用计数在 :meth:`_SharedPoller.detach` 里减；只有减到 0 才返回要停的流。
        #: 重复退订时 ``found`` 为假、``detach`` 不执行，计数不会被多减一次。
        stream = self._forget_subscription(sub_id) if found else None
        return {
            "status": "unsubscribed",
            "id": sub_id,
            "found": found,
            "_stream": stream,
        }

    def _forget_subscription(self, sub_id: str) -> Any | None:
        """把一条订阅从本连接的账上摘掉；返回"该停的流"仅当它是上游最后一个订阅者。

        幂等：``sub_id`` 已不在表里时什么都不做、返回 ``None``。锁序固定为
        ``_subs_lock`` → ``_POLLERS_LOCK``（后者只在 ``detach`` 内部短暂持有）。
        """
        with self._subs_lock:
            poller = self._subs.pop(sub_id, None)
            self._sub_symbols.pop(sub_id, None)
            #: 出处表跟着订阅一起回收：没有活跃订阅时整表清空；表本身也有容量上限，
            #: 长期反复换标的不会把它撑成慢泄漏。
            if not self._subs:
                self._sub_providers.clear()
            elif len(self._sub_providers) > MAX_TRACKED_SUB_PROVIDERS:
                for symbol in list(self._sub_providers)[
                    : len(self._sub_providers) - MAX_TRACKED_SUB_PROVIDERS
                ]:
                    del self._sub_providers[symbol]
        if poller is None:
            return None
        return poller.detach(self, sub_id)

    async def _safe_stop(self, stream: Any) -> None:
        with contextlib.suppress(Exception):
            await stream.stop()

    def _list_subscriptions(self) -> dict[str, Any]:
        # P1-9/M5：快照在锁内取——``_subscribe``（loop 线程）与退订在同一把锁下变更，
        # worker 线程的只读快照不再与变更竞速，list 迭代不会撞 RuntimeError。
        with self._subs_lock:
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
                symbols = self._sub_symbols.get(sub_id, [])
                item: dict[str, Any] = {
                    "id": sub_id,
                    "state": state_value,
                    #: 每条订阅的标的与出处 Provider：让消费方不依赖建立时的请求
                    #: 历史就能对账、退订（与 ``_sub_providers`` 同一出处口径）。
                    "symbols": list(symbols),
                }
                if symbols:
                    item["provider"] = self._sub_providers.get(symbols[0], "tdx")
                items.append(item)
        return {"subscriptions": items}

    def _on_quote(self, symbol: str, data: dict[str, Any]) -> None:
        """流式 worker 每轮轮询到快照时调用（同步回调）。

        推送帧通过 ``run_coroutine_threadsafe`` 调度到连接循环，不阻塞流式 worker；
        单条 ``send`` 失败只摘本连接的订阅（见 :meth:`_on_delivery_failure`），
        共享同一条上游轮询的其它连接照常收帧。
        """
        if self._loop is None or self._send is None:
            return
        #: 推送帧带上 ``provider`` / ``observed_at_ns`` / ``warnings``：请求-响应路径上的
        #: 结果都带 ``meta``（``serialization.serialize_result``），推送帧却只有裸 data，
        #: 消费方因此拿不到"这条数据有瑕疵"的任何信号——与同一份声明直接冲突。形状对齐
        #: 请求-响应的 ``meta``，流式与一次性查询才不会因为入口不同而丢信息。
        meta: dict[str, Any] = {"provider": self._push_provider(symbol)}
        #: 告警与观测时刻由流式层随 payload 带上来（``streaming.base._round_meta``）：
        #: 回调跑在轮询线程里，``current_warnings()`` 的 ContextVar sink 在这里本来就是
        #: 空的，过去那段读取因此永远返回 ``[]``，推送帧从来不带 ``warnings``。
        #: ``_drain_warnings()`` 只保留给「调用方自己装了 sink」的同线程场景。
        warnings = data.get("warnings") if isinstance(data, dict) else None
        if not warnings:
            warnings = self._drain_warnings()
        if warnings:
            meta["warnings"] = warnings
        observed = data.get("observed_at_ns") if isinstance(data, dict) else None
        if observed is not None:
            meta["observed_at_ns"] = observed
        #: 元信息只出现在 ``meta`` 里：流式层把它并进 payload 是为了跨层携带，到了 wire
        #: 上再留在 ``data`` 里就成了同一事实两处声明，消费方还得自己判断哪份为准。
        payload = (
            {key: value for key, value in data.items() if key not in {"warnings", "observed_at_ns"}}
            if isinstance(data, dict)
            else data
        )
        frame = {
            "jsonrpc": JSONRPC_VERSION,
            "method": PUSH_METHOD,
            "params": {"type": "snapshot", "code": symbol, "data": payload, "meta": meta},
        }
        self._schedule_push(frame)

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
        self._schedule_push(frame)

    async def _emit(self, frame: dict[str, Any]) -> None:
        if self._send is None:
            return
        try:
            await self._send(json.dumps(frame, ensure_ascii=False, default=str))
        except Exception:
            #: F-8：发送口坏了就只摘本连接。共享 poller 上还有别的订阅者，它们的
            #: 投递各自独立；把异常咽下去而不是让它把 fan-out 带塌，也不是让整条
            #: 上游轮询跟着停。
            logger.warning("WS 推送发送失败，摘除本连接的全部订阅", exc_info=True)
            self._on_delivery_failure()

    def _on_delivery_failure(self) -> None:
        """发送口已经坏了：把本连接的订阅全部摘掉（各自减引用计数），只做一次。

        幂等由 ``_delivery_broken`` 与 ``stop_all_subscriptions`` 自己的空表判断共同
        保证：并发的失败帧不会重复排空，也不会把同一条订阅减两次。
        """
        if self._delivery_broken:
            return
        self._delivery_broken = True
        self._spawn(self.stop_all_subscriptions())

    def _spawn(self, coro: Any) -> None:
        """把收尾协程挂到连接循环上；循环已关就丢掉协程，不制造孤儿任务。

        用 ``run_coroutine_threadsafe`` 而不是 ``loop.create_task``：fan-out 回调既可能
        跑在 loop 线程（异步流），也可能跑在流式 worker 线程（同步流），后者直接
        ``create_task`` 会抛 "no running event loop"。
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:  # pragma: no cover - 循环正在关闭
            coro.close()

    def _push_provider(self, symbol: str) -> str:
        # P1-9/M5：回调跑在流式 worker 线程上，出处表与订阅表同一把锁。
        with self._subs_lock:
            return self._sub_providers.get(str(symbol), "tdx")

    @staticmethod
    def _drain_warnings() -> list[dict[str, str]]:
        """当前上下文里积着的瑕疵，按请求-响应面同样的形状带出去。"""
        from ..diagnostics import current_warnings

        return [
            {"code": str(item.code.value), "message": item.message} for item in current_warnings()
        ]

    def _schedule_push(self, frame: dict[str, Any]) -> None:
        """把推送帧丢给连接循环，但**不许无限堆积**。

        过去这是纯 fire-and-forget：``run_coroutine_threadsafe`` 返回的 Future 被丢弃，
        ``_emit`` 里的 ``send`` 没有超时。慢客户端 / TCP 反压下，每个轮询节拍（默认 1s）
        都往事件循环塞一个待发协程，而流式 worker 线程不会因此减速——待发任务与内存单调
        增长，没有丢弃、没有背压，与流式侧自己的 ``BackpressureQueue`` 口径完全不一致。

        现在在飞帧数有上限：超阈值就丢最旧的一帧并记日志（推送是连续的实时快照，丢一帧
        远好过让整条连接的内存无界上涨）。
        """
        if self._loop is None or self._send is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._emit(frame), self._loop)
        except Exception:  # pragma: no cover - 连接已断等情况
            logger.exception("WS 推送帧调度失败")
            return
        pending = self._pending_pushes
        #: 先摘掉已经跑完的，否则计数把"早就发完的帧"也算成积压。
        if len(pending) >= MAX_PENDING_PUSH_FRAMES:
            pending[:] = [item for item in pending if not item.done()]
        pending.append(future)
        overflow = len(pending) - MAX_PENDING_PUSH_FRAMES
        if overflow > 0:
            # P0-5：被丢弃的帧必须 cancel——只删引用的话，协程已在 loop 队列里，
            # 帧照样发送、内存照样涨，"慢客户端反压"并不存在。
            dropped = pending[:overflow]
            del pending[:overflow]
            for item in dropped:
                item.cancel()
            logger.warning(
                "WS 推送积压超过 %d 帧，丢弃最旧的 %d 帧（慢客户端反压）",
                MAX_PENDING_PUSH_FRAMES,
                overflow,
            )

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
