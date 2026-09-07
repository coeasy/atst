# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""JSON-RPC 2.0 stdio framework for the tstdx MCP server.

Implements the newline-delimited JSON-RPC 2.0 loop (``initialize`` /
``ping`` / ``tools/list`` / ``tools/call`` / ``shutdown``), response
serialization with bounded payloads, and the :class:`MCPServer`
assembly.  Tool definitions live in :mod:`._tools_spec`, their
implementations in :mod:`._tools_impl`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import threading
from typing import Any

from ...client import TdxClient
from ...errors import TdxError
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

# 历史兼容：沿用拆分前 ``tstdx.integration.mcp_server`` 的 logger 名，
# 外部 logging 过滤器 / 既有日志文案不受 P11-1 拆分影响。
logger = logging.getLogger("tstdx.integration.mcp_server")


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #
def _cap_rows(data: Any) -> Any:
    """Truncate list payloads to ``MAX_ROWS`` rows."""
    if isinstance(data, list) and len(data) > MAX_ROWS:
        return data[:MAX_ROWS]
    return data


def _serialize_to_text(data: Any) -> str:
    """JSON-serialize ``data`` to a bounded text string."""
    data = _cap_rows(data)
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > MAX_TEXT_CHARS:
        text = text[: MAX_TEXT_CHARS - 64] + '" ... [truncated]'
        # Repair JSON: wrap in an object that still parses.
        text = json.dumps({"truncated": True, "preview": text}, ensure_ascii=False)
    return text


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
class MCPServer:
    """Standalone stdio JSON-RPC 2.0 MCP server for tstdx.

    Parameters
    ----------
    client:
        Optional pre-built :class:`~tstdx.client.TdxClient`.  When
        ``None`` (default) a new client is created lazily on first
        tool call, so constructing the server itself is network-free.
    facade:
        Optional pre-built :class:`~tstdx.facade.api.UnifiedQuoteAPI`.
        ``None`` (default) creates one lazily for cross-source / web-only
        tools (``use_facade=True``).

    Design notes
    ------------
    * :meth:`handle_request` is a **pure function** that can be unit
      tested without touching stdio.
    * :meth:`serve` is the main loop that reads newline-delimited JSON
      from ``sys.stdin``, calls :meth:`handle_request`, and writes
      responses to ``sys.stdout``.  It never prints anything else to
      stdout — diagnostics go to ``sys.stderr``.
    * :meth:`stop` is best-effort: it sets a flag that the serve loop
      checks between iterations, and closes the stdin handle so a
      blocking read unblocks.
    """

    def __init__(self, client: TdxClient | None = None, facade: Any | None = None) -> None:
        self._client = client
        self._facade = facade
        self._stopped = threading.Event()

    # -- lifecycle ---------------------------------------------------------- #
    def _get_client(self) -> TdxClient:
        if self._client is None:
            self._client = TdxClient()
        return self._client

    def _facade_obj(self) -> Any:  # noqa: ANN401
        """门面（:class:`~tstdx.facade.api.UnifiedQuoteAPI`），惰性创建。

        复权 / 全市场 / 板块 / 分钟 K 线 / 扩展市场 / 搜索等跨源方法统一
        走门面，复用其自动路由与连接管理；单连接独立实例（测试注入 fake
        即可离线）。
        """
        if self._facade is None:
            from ...facade.api import UnifiedQuoteAPI

            self._facade = UnifiedQuoteAPI()
        return self._facade

    def stop(self) -> None:
        """Signal graceful shutdown.

        Idempotent.  Safe to call from another thread.  Note that a
        fully blocking ``stdin.read()`` will only return once the
        handle is closed, so callers may also close stdin externally.
        """
        self._stopped.set()

    def serve(self) -> None:
        """Main stdio loop.

        Reads one JSON object per line from ``sys.stdin``, dispatches
        through :meth:`handle_request`, writes responses to
        ``sys.stdout`` and flushes.  Exits on EOF, on
        :meth:`stop`, or on ``KeyboardInterrupt``.
        """
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
                    break  # EOF
                stripped = line.strip()
                if not stripped:
                    continue  # skip blank lines
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
                                "message": f"Parse error: {exc}",
                            },
                        }
                    )
                    continue
                response = self.handle_request(request)
                if response is not None:
                    self._write(response)
        except KeyboardInterrupt:  # pragma: no cover - depends on tty
            pass
        except Exception as exc:  # noqa: BLE001 —— stdio 循环异常只进日志（V6）
            logger.error("mcp_server: stdio loop crashed: %s", exc, exc_info=exc)
        finally:
            with contextlib.suppress(OSError, ValueError):
                stdout.flush()

    @staticmethod
    def _write(obj: dict[str, Any]) -> None:
        """Emit one JSON line to stdout.  Never raises on stdout errors."""
        try:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):  # pragma: no cover
            pass

    # -- dispatch ----------------------------------------------------------- #
    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC request or notification.

        Returns
        -------
        dict | None
            A JSON-RPC response dict for requests, or ``None`` for
            notifications (which MUST NOT get a response per JSON-RPC).
        """
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": ERR_INVALID_REQUEST,
                    "message": "Request must be a JSON object",
                },
            }

        method = request.get("method")
        rid = request.get("id")
        is_notification = rid is None

        # --- notifications ------------------------------------------------- #
        if is_notification:
            # notifications/initialized and any other notification: no response
            return None

        # --- requests ------------------------------------------------------ #
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
            # TdxError 属预期错误：保留 code 与 message（context 进 data）
            return self._error(
                rid,
                ERR_INTERNAL,
                str(exc),
                data={"code": getattr(exc, "code", ""), "context": getattr(exc, "context", {})},
            )
        except KeyError as exc:
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                f"Missing required parameter: {exc.args[0]!r}",
            )
        except Exception as exc:
            # 原生异常不外泄细节（V6）：对外 generic，详情进日志
            logger.error("mcp_server request failed (method=%s): %s", method, exc, exc_info=exc)
            return self._error(rid, ERR_INTERNAL, "internal error")

        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": result,
        }

    # -- method handlers ---------------------------------------------------- #
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
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown tool: {name!r}",
                    }
                ],
                "isError": True,
            }
        spec = _TOOLS_BY_NAME[name]
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return {
                "content": [{"type": "text", "text": "arguments must be an object"}],
                "isError": True,
            }
        if spec.use_facade:
            target: Any = self._facade_obj()
        else:
            target = self._get_client()
        try:
            data = spec.handler(target, args)
        except TdxError:
            raise
        except Exception as exc:
            # 原生异常统一包装：对外 generic message，细节进日志（V6）
            logger.error("tool %s failed: %s", name, exc, exc_info=exc)
            raise TdxError("internal error") from exc
        return {
            "content": [{"type": "text", "text": _serialize_to_text(data)}],
            "isError": False,
        }

    # -- error helper ------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def create_mcp_server(client: TdxClient | None = None, facade: Any | None = None) -> MCPServer:
    """Create an :class:`MCPServer` (convenience factory).

    Parameters
    ----------
    client:
        Optional pre-built :class:`~tstdx.client.TdxClient`.  ``None``
        triggers lazy creation on first tool call.
    facade:
        Optional pre-built :class:`~tstdx.facade.api.UnifiedQuoteAPI`.
        ``None`` triggers lazy creation for cross-source / web-only tools.
    """
    return MCPServer(client=client, facade=facade)


# --------------------------------------------------------------------------- #
# Module entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover - direct execution
    create_mcp_server().serve()
