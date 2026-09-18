# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 JSON-RPC handler."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from ..client_api import Client
from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..providers import PROVIDERS
from ..runtime.orchestration import FallbackPolicy
from .serialization import serialize_result

__all__ = ["RuntimeJsonRpcHandler"]

JSONRPC_VERSION = "2.0"
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603


class RuntimeJsonRpcHandler:
    METHODS = frozenset({
        "quotes", "bars", "snapshot", "minute", "trades",
        "security.count", "security.list",
        "query", "runtime.capabilities", "runtime.health",
    })

    def __init__(self, client: Client | None = None) -> None:
        self.client = client or Client()

    def handle_message(self, raw: str | bytes) -> str | None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json.dumps(self._error(None, ERR_PARSE, "parse error", ValidationError("invalid JSON")), ensure_ascii=False)
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            return json.dumps(self._error(message.get("id") if isinstance(message, dict) else None, ERR_INVALID_REQUEST, "invalid request", ValidationError("invalid JSON-RPC request")), ensure_ascii=False)
        request_id = message.get("id")
        method = str(message.get("method", ""))
        params = message.get("params") or {}
        if request_id is None:
            with contextlib.suppress(Exception):
                self._dispatch(method, params if isinstance(params, dict) else {})
            return None
        if method not in self.METHODS:
            return json.dumps(self._error(request_id, ERR_METHOD_NOT_FOUND, "method not found"), ensure_ascii=False)
        if not isinstance(params, dict):
            return json.dumps(self._error(request_id, ERR_INVALID_PARAMS, "invalid params", ValidationError("params must be an object")), ensure_ascii=False)
        try:
            result = self._dispatch(method, params)
        except ValidationError as exc:
            return json.dumps(self._error(request_id, ERR_INVALID_PARAMS, "invalid params", exc), ensure_ascii=False)
        except Exception as exc:
            return json.dumps(self._error(request_id, ERR_INTERNAL, "request failed", exc), ensure_ascii=False)
        return json.dumps({"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}, ensure_ascii=False, default=str)

    @staticmethod
    def _policy(params: dict[str, Any]) -> FallbackPolicy | None:
        raw = params.get("fallback")
        if raw in (None, "", []):
            return None
        if isinstance(raw, str):
            values = [item.strip() for item in raw.split(",") if item.strip()]
        elif isinstance(raw, (list, tuple)):
            values = [str(item).strip() for item in raw if str(item).strip()]
        else:
            raise ValidationError("fallback must be a provider list")
        return FallbackPolicy.build(*values)

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime.health":
            return {"status": "ok", "api": "v13", "default_provider": self.client.runtime.planner.default_provider, "migrated_capabilities": len(self.client.capabilities())}
        if method == "runtime.capabilities":
            return {
                "capabilities": list(self.client.capabilities()),
                "providers": {
                    provider: {channel.id: sorted(channel.capabilities) for channel in PROVIDERS.get(provider).channels}
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
            return serialize_result(self.client.call(
                capability,
                *args,
                provider=params.get("provider"),
                channel=params.get("channel"),
                currentness=str(params.get("currentness", "business")),
                max_age=params.get("max_age"),
                **kwargs,
            ))

        provider = params.get("provider")
        if method == "quotes":
            symbols = params.get("symbols")
            if not isinstance(symbols, (str, list, tuple)) or not symbols:
                raise ValidationError("symbols is required")
            return serialize_result(self.client.quotes(symbols, provider=provider, policy=self._policy(params), currentness="live", max_age=params.get("max_age")))
        if method == "bars":
            symbol = params.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            return serialize_result(self.client.bars(symbol, provider=provider, policy=self._policy(params), period=str(params.get("period", "day")), count=int(params.get("count", 320)), start=int(params.get("start", 0)), adjustment=str(params.get("adjustment", "")), currentness="historical", max_age=params.get("max_age")))

        symbol = params.get("symbol")
        if method in {"snapshot", "minute", "trades"}:
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            chosen = str(provider or "tdx")
            if method == "snapshot":
                return serialize_result(self.client.snapshot(symbol, provider=chosen))
            if method == "minute":
                return serialize_result(self.client.minute(symbol, provider=chosen))
            return serialize_result(self.client.trades(symbol, provider=chosen, start=int(params.get("start", 0)), count=int(params.get("count", 0))))

        chosen = str(provider or "tdx")
        market = params.get("market", 0)
        if method == "security.count":
            return serialize_result(self.client.security_count(market=market, provider=chosen))
        if method == "security.list":
            return serialize_result(self.client.security_list(market=market, start=int(params.get("start", 0)), provider=chosen))
        raise RuntimeError("unreachable")

    @staticmethod
    def _error(request_id: Any, rpc_code: int, message: str, exc: Exception | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": rpc_code, "message": message}
        if exc is not None:
            # Attach the envelope with the request identity so a WS client can
            # correlate the failure the same way HTTP/MCP/CLI clients do.
            error["data"] = to_error_envelope(
                exc,
                request_id=None if request_id is None else str(request_id),
            ).to_dict()
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}
