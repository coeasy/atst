# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""JSON-RPC 2.0 stdio framework for the canonical v13 MCP adapter."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
from typing import Any

from ...client_api import Client
from ...error_envelope import to_error_envelope
from ...errors import InternalError, TdxError, ValidationError
from ._common import (
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE,
    MAX_ROWS,
    MAX_TEXT_CHARS,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
)
from ._tools_spec import _TOOLS_BY_NAME, TOOLS

__all__ = ["MCPServer", "create_mcp_server"]

logger = logging.getLogger("tstdx.integration.mcp_server")


def _cap_rows(data: Any) -> Any:
    if isinstance(data, list) and len(data) > MAX_ROWS:
        return data[:MAX_ROWS]
    return data


def _serialize_to_text(data: Any) -> str:
    data = _cap_rows(data)
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - 64] + '" ... [truncated]'
        text = json.dumps({"truncated": True, "preview": text}, ensure_ascii=False)
    return text


class MCPServer:
    """Standalone MCP server backed by the same v13 Client as every surface."""

    def __init__(self, client: Client | None = None) -> None:
        self._client = client or Client()
        self._owns_client = client is None
        self._stopped = threading.Event()

    def stop(self) -> None:
        # Idempotent: an owned transport must be closed exactly once even if
        # ``shutdown`` and the ``serve`` epilogue both race to stop the server.
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._owns_client:
            self._owns_client = False
            with contextlib.suppress(Exception):
                self._client.close()

    def serve(self) -> None:
        stdin = sys.stdin
        stdout = sys.stdout
        try:
            while not self._stopped.is_set():
                try:
                    line = stdin.readline()
                except (OSError, ValueError):
                    logger.error("mcp_server: stdin read failed, exiting loop")
                    break
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    request = json.loads(stripped)
                except json.JSONDecodeError:
                    self._write(
                        self._protocol_error(None, ERR_PARSE, "Parse error")
                    )
                    continue
                response = self.handle_request(request)
                if response is not None:
                    self._write(response)
        except KeyboardInterrupt:  # pragma: no cover
            pass
        except Exception as exc:  # pragma: no cover
            logger.error("mcp_server: stdio loop crashed: %s", exc, exc_info=exc)
        finally:
            if self._owns_client:
                with contextlib.suppress(Exception):
                    self._client.close()
            with contextlib.suppress(OSError, ValueError):
                stdout.flush()

    @staticmethod
    def _write(obj: dict[str, Any]) -> None:
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):  # pragma: no cover
            pass

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._protocol_error(None, ERR_INVALID_REQUEST, "Invalid Request")

        method = request.get("method")
        rid = request.get("id")
        if rid is None:
            return None
        try:
            if method == "initialize":
                result = self._handle_initialize()
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool.to_dict() for tool in TOOLS]}
            elif method == "tools/call":
                result = self._handle_tools_call(request.get("params") or {})
            elif method == "shutdown":
                result = {}
                self.stop()
            else:
                return self._protocol_error(
                    rid,
                    ERR_METHOD_NOT_FOUND,
                    f"Method not found: {method!r}",
                    method=method,
                )
        except ValidationError as exc:
            # Invalid tool parameters are a client error, not an internal one:
            # JSON-RPC ``-32602`` keeps the distinction observable to callers.
            envelope = to_error_envelope(exc, request_id=str(rid))
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                envelope.message,
                data=envelope.to_dict(),
            )
        except TdxError as exc:
            envelope = to_error_envelope(exc, request_id=str(rid))
            return self._error(rid, ERR_INTERNAL, envelope.message, data=envelope.to_dict())
        except KeyError as exc:
            envelope = to_error_envelope(
                ValidationError(
                    "missing required parameter",
                    context={"phase": "mcp"},
                    cause=exc,
                ),
                request_id=str(rid),
            )
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                "Missing required parameter",
                data=envelope.to_dict(),
            )
        except Exception as exc:
            logger.error("mcp_server request failed (method=%s): %s", method, exc, exc_info=exc)
            envelope = to_error_envelope(exc, phase="mcp", request_id=str(rid))
            return self._error(rid, ERR_INTERNAL, envelope.message, data=envelope.to_dict())
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _protocol_error(
        self,
        rid: Any,
        code: int,
        message: str,
        *,
        method: Any = None,
    ) -> dict[str, Any]:
        """Protocol-level failure, with the canonical error envelope attached.

        Every failure surface — parse, request shape, method dispatch — answers
        with the same envelope contract as capability failures, so a client can
        read ``phase`` / ``request_id`` / fail-closed permits uniformly instead
        of special-casing the transport.
        """

        context: dict[str, Any] = {
            "phase": "mcp_protocol",
            "fallback": False,
            "provider_switch_allowed": False,
        }
        if method is not None:
            context["method"] = str(method)
        envelope = to_error_envelope(
            ValidationError(message, context=context),
            request_id=None if rid is None else str(rid),
        )
        return self._error(rid, code, message, data=envelope.to_dict())

    @staticmethod
    def _handle_initialize() -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or name not in _TOOLS_BY_NAME:
            return {
                "content": [{"type": "text", "text": f"Unknown tool: {name!r}"}],
                "isError": True,
            }
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return {
                "content": [{"type": "text", "text": "arguments must be an object"}],
                "isError": True,
            }
        try:
            data = _TOOLS_BY_NAME[name].handler(self._client, args)
        except KeyError as exc:
            # A missing required tool argument is a client error (-32602), not an
            # internal fault; surface it as such instead of collapsing to E9000.
            raise ValidationError(
                f"missing required parameter for tool {name!r}",
                context={"phase": "mcp", "capability": name},
                cause=exc,
            ) from exc
        except TdxError:
            raise
        except Exception as exc:
            raise InternalError(
                "internal error",
                context={"phase": "mcp", "capability": name},
                cause=exc,
            ) from exc
        return {
            "content": [{"type": "text", "text": _serialize_to_text(data)}],
            "isError": False,
        }

    @staticmethod
    def _error(
        rid: Any,
        code: int,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": rid, "error": err}


def create_mcp_server(client: Client | None = None) -> MCPServer:
    return MCPServer(client=client)


if __name__ == "__main__":  # pragma: no cover
    create_mcp_server().serve()
