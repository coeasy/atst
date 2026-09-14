# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical WebSocket boundary over the legacy JSON-RPC method surface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from ..error_envelope import to_error_envelope
from ..errors import TdxError, ValidationError
from . import ws_server as legacy
from .http_runtime import ProviderHttpClient
from .ws_server import WsConfig as WsConfig

__all__ = ["JsonRpcHandler", "WsConfig", "serve_ws"]

_LOG = logging.getLogger(__name__)


class JsonRpcHandler(legacy.JsonRpcHandler):
    """Legacy method surface with planned execution and canonical errors."""

    _client: Any
    _facade: Any

    @staticmethod
    def _request_id(req_id: Any) -> str | None:
        return None if req_id is None else str(req_id)

    def _rpc_error(
        self,
        req_id: Any,
        rpc_code: int,
        exc: BaseException,
        *,
        phase: str,
    ) -> dict[str, Any]:
        envelope = to_error_envelope(
            exc,
            phase=phase,
            request_id=self._request_id(req_id),
        )
        return {
            "jsonrpc": legacy.JSONRPC_VERSION,
            "id": req_id,
            "error": {
                "code": rpc_code,
                "message": envelope.message,
                "data": envelope.to_dict(),
            },
        }

    def handle_message(self, raw: str | bytes) -> str | None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            msg = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            validation = ValidationError(
                "parse error",
                context={"phase": "ws_protocol"},
                cause=exc,
            )
            return json.dumps(
                self._rpc_error(None, legacy.ERR_PARSE, validation, phase="ws_protocol"),
                ensure_ascii=False,
            )

        if isinstance(msg, list):
            replies = [reply for reply in (self._handle_one(item) for item in msg) if reply is not None]
            return json.dumps(replies, ensure_ascii=False) if replies else None
        reply = self._handle_one(msg)
        return json.dumps(reply, ensure_ascii=False) if reply is not None else None

    def _handle_one(self, msg: Any) -> dict[str, Any] | None:
        if (
            not isinstance(msg, dict)
            or msg.get("jsonrpc") != legacy.JSONRPC_VERSION
            or "method" not in msg
        ):
            req_id = msg.get("id") if isinstance(msg, dict) else None
            validation = ValidationError(
                "invalid request",
                context={"phase": "ws_protocol"},
            )
            return self._rpc_error(
                req_id,
                legacy.ERR_INVALID_REQUEST,
                validation,
                phase="ws_protocol",
            )

        method = msg["method"]
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if req_id is None:
            # JSON-RPC notifications never receive responses. Domain/native
            # request failures must therefore be logged and contained locally
            # instead of escaping and tearing down the WebSocket connection.
            # BaseException control-flow signals are intentionally not caught.
            try:
                self._dispatch(method, params)
            except (TdxError, ValueError, KeyError, TypeError) as exc:
                _LOG.debug("websocket notification rejected (method=%s): %s", method, exc)
            except Exception:
                _LOG.exception("websocket notification failed (method=%s)", method)
            return None

        try:
            result = self._dispatch(method, params)
        except legacy._MethodNotFound as exc:
            validation = ValidationError(
                f"method not found: {method}",
                context={"phase": "ws_protocol", "method": method},
                cause=exc,
            )
            return self._rpc_error(
                req_id,
                legacy.ERR_METHOD_NOT_FOUND,
                validation,
                phase="ws_protocol",
            )
        except legacy._InvalidParams as exc:
            validation = ValidationError(
                str(exc),
                context={"phase": "ws_protocol", "method": method},
                cause=exc,
            )
            return self._rpc_error(
                req_id,
                legacy.ERR_INVALID_PARAMS,
                validation,
                phase="ws_protocol",
            )
        except TdxError as exc:
            return self._rpc_error(req_id, -32000, exc, phase="ws")
        except Exception as exc:
            _LOG.exception("websocket dispatch failed (method=%s)", method)
            return self._rpc_error(req_id, legacy.ERR_INTERNAL, exc, phase="ws")
        return {
            "jsonrpc": legacy.JSONRPC_VERSION,
            "id": req_id,
            "result": result,
        }

    def _client_obj(self) -> Any:  # noqa: ANN401
        if self._client is None:
            self._client = ProviderHttpClient()
        return self._client

    def _facade_obj(self) -> Any:  # noqa: ANN401
        if self._facade is None:
            from ..facade import UnifiedQuoteAPI

            self._facade = UnifiedQuoteAPI()
        return self._facade

    def _m_stock_changes(self, params: dict) -> Any:  # noqa: ANN401
        """Route Eastmoney stock changes through the canonical Provider runtime."""
        raw = params.get("types") or []
        if not isinstance(raw, (list, tuple)) or not all(isinstance(item, int) for item in raw):
            raise legacy._InvalidParams(
                "types must be a list of integers (e.g. [8201, 8193])"
            )
        client = self._client_obj()
        return client.service.eastmoney.stock_changes(
            tuple(raw),
            page=int(params.get("page", 1)),
            size=int(params.get("size", 50)),
        )


def serve_ws(
    handler: JsonRpcHandler | None = None,
    config: WsConfig | None = None,
) -> Any:  # pragma: no cover - requires a real socket
    """Start the established WS transport using canonical handlers per connection."""

    try:
        ws_serve, modern = legacy._import_ws_serve()
    except ImportError as exc:
        raise RuntimeError("WebSocket 服务需要: pip install tstdx[server]") from exc

    if handler is None:
        template = JsonRpcHandler()
    elif isinstance(handler, JsonRpcHandler):
        template = handler
    else:
        template = JsonRpcHandler(
            client=getattr(handler, "_client", None),
            facade=getattr(handler, "_facade", None),
        )

    if template._client is None:
        template._client = ProviderHttpClient()
    if template._facade is None:
        from ..facade import UnifiedQuoteAPI

        template._facade = UnifiedQuoteAPI()

    cfg = config or WsConfig()
    connections: dict[Any, JsonRpcHandler] = {}

    def _modern_process_request(ws: Any, request: Any) -> Any:  # noqa: ARG001
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        if request.path != cfg.path:
            return Response(403, "Forbidden", Headers(), b"forbidden: invalid path\n")
        return None

    def _legacy_process_request(path: str, request_headers: Any) -> Any:  # noqa: ARG001
        if path != cfg.path:
            return (403, [("Content-Type", "text/plain")], b"forbidden: invalid path\n")
        return None

    process_request: Any = _modern_process_request if modern else _legacy_process_request

    async def _connection(ws: Any, _path: str | None = None) -> None:
        conn_handler = JsonRpcHandler(client=template._client, facade=template._facade)
        connections[ws] = conn_handler
        try:
            async for raw in ws:
                reply, added = await asyncio.to_thread(conn_handler.on_message, raw)
                if reply is not None:
                    await ws.send(reply)
                if added:
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
                    symbols = conn_handler.subscription_snapshot()
                    if not symbols:
                        continue
                    notes = await asyncio.to_thread(conn_handler.push_quote_updates, symbols)
                    for note in notes:
                        await ws.send(note)
                except Exception as exc:
                    _LOG.debug("ws push loop: send failed: %s", exc)

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
        server._tstdx_push_task = push_task  # noqa: SLF001 - graceful shutdown hook
        return server

    return _main()
