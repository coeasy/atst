# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""JSON-RPC 2.0 stdio framework for the tstdx MCP server."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
from typing import Any

from ...client import TdxClient
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
    """Standalone stdio JSON-RPC 2.0 MCP server for tstdx."""

    def __init__(self, client: TdxClient | None = None, facade: Any | None = None) -> None:
        self._client = client
        self._facade = facade
        self._stopped = threading.Event()

    def _get_client(self) -> TdxClient:
        if self._client is None:
            self._client = TdxClient()
        return self._client

    def _facade_obj(self) -> Any:
        if self._facade is None:
            from ...facade.api import UnifiedQuoteAPI

            self._facade = UnifiedQuoteAPI()
        return self._facade

    def stop(self) -> None:
        self._stopped.set()

    def serve(self) -> None:
        stdin = sys.stdin
        stdout = sys.stdout
        try:
            while not self._stopped.is_set():
                try:
                    line = stdin.readline()
                except (OSError, ValueError):
                    logger.error("mcp_server: stdin read failed, exiting loop.")
                    break
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    request = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    logger.debug("mcp_server: parse error from stdin: %s", exc)
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {
                                "code": ERR_PARSE,
                                "message": "Parse error",
                                "data": {
                                    "code": "E1010",
                                    "error": "ValidationError",
                                    "message": "invalid JSON",
                                    "http_status": 422,
                                    "retryable": False,
                                    "context": {},
                                },
                            },
                        }
                    )
                    continue
                response = self.handle_request(request)
                if response is not None:
                    self._write(response)
        except KeyboardInterrupt:  # pragma: no cover
            pass
        except Exception as exc:  # noqa: BLE001
            logger.error("mcp_server: stdio loop crashed: %s", exc, exc_info=exc)
        finally:
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
                result = self._handle_initialize(request.get("params") or {})
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.to_dict() for t in TOOLS]}
            elif method == "tools/call":
                result = self._handle_tools_call(request.get("params") or {})
            elif method == "shutdown":
                result = {}
                self.stop()
            else:
                return self._error(
                    rid,
                    ERR_METHOD_NOT_FOUND,
                    f"Method not found: {method!r}",
                )
        except TdxError as exc:
            envelope = to_error_envelope(exc)
            return self._error(
                rid,
                ERR_INTERNAL,
                envelope.message,
                data=envelope.to_dict(),
            )
        except KeyError as exc:
            domain = ValidationError(
                "missing required parameter",
                context={"phase": "mcp", "request_id": str(rid)},
                cause=exc,
            )
            envelope = to_error_envelope(domain)
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                "Missing required parameter",
                data=envelope.to_dict(),
            )
        except Exception as exc:
            logger.error("mcp_server request failed (method=%s): %s", method, exc, exc_info=exc)
            envelope = to_error_envelope(exc)
            return self._error(
                rid,
                ERR_INTERNAL,
                envelope.message,
                data=envelope.to_dict(),
            )

        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
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
        spec = _TOOLS_BY_NAME[name]
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return {
                "content": [{"type": "text", "text": "arguments must be an object"}],
                "isError": True,
            }
        target: Any = self._facade_obj() if spec.use_facade else self._get_client()
        try:
            data = spec.handler(target, args)
        except TdxError:
            raise
        except Exception as exc:
            logger.error("tool %s failed: %s", name, exc, exc_info=exc)
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


def create_mcp_server(client: TdxClient | None = None, facade: Any | None = None) -> MCPServer:
    return MCPServer(client=client, facade=facade)


if __name__ == "__main__":  # pragma: no cover
    create_mcp_server().serve()
