# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 JSON-RPC handler."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from ..client.api import Client
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

JSONRPC_VERSION = "2.0"
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603


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
        }
    )

    def __init__(self, client: Client | None = None) -> None:
        self.client = client or Client()
        #: 传入的 client 归调用方，自己造的才由 :meth:`close` 释放——与 MCP 面
        #: ``MCPServer._owns_client``（``_server.py``）同一口径。
        self._owns_client = client is None

    def close(self) -> None:
        """释放本处理器自建的 ``Client``（连带它的连接池）。幂等。

        过去 WS 面没有任何释放入口：托管进程退出时这条 ``Client`` 一路 socket 与
        心跳线程原地留下（第 26 轮 F-100）。
        """
        if self._owns_client:
            self._owns_client = False
            self.client.close()

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
            return {
                "status": "ok",
                "api": "v13",
                "default_provider": self.client.runtime.planner.default_provider,
                "migrated_capabilities": len(self.client.capabilities()),
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
            chosen = str(provider or "tdx")
            if method == "snapshot":
                return serialize_result(self.client.snapshot(symbol, provider=chosen))
            if method == "minute":
                return serialize_result(self.client.minute(symbol, provider=chosen))
            return serialize_result(
                self.client.trades(
                    symbol,
                    provider=chosen,
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
