# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Strict Provider-first JSON-RPC handler for WebSocket v2."""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..runtime import UnifiedRuntime

__all__ = ["RuntimeJsonRpcHandler"]

JSONRPC_VERSION = "2.0"
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _data(result: Any) -> Any:
    return {
        "data": _jsonable(result.data),
        "meta": {
            "provider": result.meta.provider,
            "channel": result.meta.channel,
            "capability": result.meta.capability,
            "fingerprint": result.meta.fingerprint,
            "provenance": result.meta.provenance.kind.value,
            "cache_tier": result.meta.provenance.cache_tier,
        },
    }


class RuntimeJsonRpcHandler:
    METHODS = frozenset({"quotes", "bars", "runtime.health"})

    def __init__(self, runtime: UnifiedRuntime | None = None) -> None:
        self.runtime = runtime or UnifiedRuntime()

    def handle_message(self, raw: str | bytes) -> str | None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json.dumps(
                self._error(None, ERR_PARSE, "parse error", ValidationError("invalid JSON")),
                ensure_ascii=False,
            )

        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            return json.dumps(
                self._error(
                    message.get("id") if isinstance(message, dict) else None,
                    ERR_INVALID_REQUEST,
                    "invalid request",
                    ValidationError("invalid JSON-RPC request"),
                ),
                ensure_ascii=False,
            )

        request_id = message.get("id")
        if request_id is None:
            with contextlib.suppress(Exception):
                self._dispatch(str(message.get("method", "")), message.get("params") or {})
            return None

        method = str(message.get("method", ""))
        params = message.get("params") or {}
        if method not in self.METHODS:
            return json.dumps(
                self._error(request_id, ERR_METHOD_NOT_FOUND, "method not found"),
                ensure_ascii=False,
            )
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
                self._error(request_id, ERR_INTERNAL, "request failed", exc),
                ensure_ascii=False,
            )
        return json.dumps(
            {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result},
            ensure_ascii=False,
            default=str,
        )

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "runtime.health":
            return {
                "status": "ok",
                "default_provider": self.runtime.planner.default_provider,
            }
        if method == "quotes":
            symbols = params.get("symbols")
            if not isinstance(symbols, (str, list, tuple)) or not symbols:
                raise ValidationError("symbols is required")
            return _data(
                self.runtime.quotes(
                    symbols,
                    provider=params.get("provider"),
                    currentness="live",
                    max_age=params.get("max_age"),
                    use_cache=bool(params.get("use_cache", True)),
                )
            )
        if method == "bars":
            symbol = params.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ValidationError("symbol is required")
            return _data(
                self.runtime.bars(
                    symbol,
                    provider=params.get("provider"),
                    period=str(params.get("period", "day")),
                    count=int(params.get("count", 320)),
                    start=int(params.get("start", 0)),
                    adjustment=str(params.get("adjustment", "")),
                    currentness="historical",
                    max_age=params.get("max_age"),
                    use_cache=bool(params.get("use_cache", True)),
                )
            )
        raise RuntimeError("unreachable")

    @staticmethod
    def _error(
        request_id: Any,
        rpc_code: int,
        message: str,
        exc: Exception | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": rpc_code, "message": message}
        if exc is not None:
            error["data"] = to_error_envelope(exc).to_dict()
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}
