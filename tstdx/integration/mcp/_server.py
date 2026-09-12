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
        self._stopped.set()
        if self._owns_client:
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
                    envelope = to_error_envelope(ValidationError("invalid JSON"))
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {
                                "code": ERR_PARSE,
                                "message": "Parse error",
                                "data": envelope.to_dict(),
                            },
                        }
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
            envelope = to_error_envelope(ValidationError("Request must be a JSON object"))
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": ERR_INVALID_REQUEST,
                    "message": "Invalid Request",
                    "data": envelope.to_dict(),
                },
            }

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
                return self._error(rid, ERR_METHOD_NOT_FOUND, f"Method not found: {method!r}")
        except TdxError as exc:
            envelope = to_error_envelope(exc)
            return self._error(rid, ERR_INTERNAL, envelope.message, data=envelope.to_dict())
        except KeyError as exc:
            envelope = to_error_envelope(
                ValidationError(
                    "missing required parameter",
                    context={"phase": "mcp", "request_id": str(rid)},
                    cause=exc,
                )
            )
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                "Missing required parameter",
                data=envelope.to_dict(),
            )
        except Exception as exc:
            logger.error("mcp_server request failed (method=%s): %s", method, exc, exc_info=exc)
            envelope = to_error_envelope(exc)
            return self._error(rid, ERR_INTERNAL, envelope.message, data=envelope.to_dict())
        return {"jsonrpc": "2.0", "id": rid, "result": result}

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
