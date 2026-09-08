# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical MCP boundary over the legacy stdio protocol implementation."""

from __future__ import annotations

import logging
from typing import Any

from ..error_envelope import to_error_envelope
from ..errors import InternalError, TdxError, ValidationError
from .http_runtime import ProviderHttpClient
from .mcp._common import (
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
)
from .mcp._server import MCPServer as LegacyMCPServer
from .mcp._server import _serialize_to_text
from .mcp._tools_spec import _TOOLS_BY_NAME, TOOLS

__all__ = ["MCPServer", "create_mcp_server"]

_LOG = logging.getLogger(__name__)


class _MCPProviderClient(ProviderHttpClient):
    """Legacy-tool-shaped client whose market-data core is PlannedService."""

    def catalog(self, symbol: str) -> Any:
        return self.f10_catalog(symbol)


class MCPServer(LegacyMCPServer):
    """MCP server whose data path and public failures use canonical v12 contracts."""

    def _get_client(self) -> Any:  # noqa: ANN401
        if self._client is None:
            self._client = _MCPProviderClient()
        return self._client

    def _facade_obj(self) -> Any:  # noqa: ANN401
        if self._facade is None:
            from ..facade import UnifiedQuoteAPI

            self._facade = UnifiedQuoteAPI()
        return self._facade

    @staticmethod
    def _request_id(rid: Any) -> str | None:
        return None if rid is None else str(rid)

    def _domain_error(
        self,
        rid: Any,
        exc: BaseException,
        *,
        phase: str,
    ) -> dict[str, Any]:
        envelope = to_error_envelope(
            exc,
            phase=phase,
            request_id=self._request_id(rid),
        )
        return self._error(
            rid,
            ERR_INTERNAL,
            envelope.message,
            data=envelope.to_dict(),
        )

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            exc = ValidationError(
                "Request must be a JSON object",
                context={"phase": "mcp_protocol"},
            )
            envelope = to_error_envelope(exc, phase="mcp_protocol")
            return self._error(
                None,
                ERR_INVALID_REQUEST,
                envelope.message,
                data=envelope.to_dict(),
            )

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
                result = {"tools": [tool.to_dict() for tool in TOOLS]}
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
            return self._domain_error(rid, exc, phase="mcp")
        except KeyError as exc:
            validation = ValidationError(
                f"Missing required parameter: {exc.args[0]!r}",
                context={"phase": "mcp", "method": method},
                cause=exc,
            )
            envelope = to_error_envelope(
                validation,
                phase="mcp",
                request_id=self._request_id(rid),
            )
            return self._error(
                rid,
                ERR_INVALID_PARAMS,
                envelope.message,
                data=envelope.to_dict(),
            )
        except Exception as exc:
            _LOG.exception("MCP request failed (method=%s)", method)
            return self._domain_error(rid, exc, phase="mcp")

        return {"jsonrpc": "2.0", "id": rid, "result": result}

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
            _LOG.exception("MCP tool %s failed", name)
            raise InternalError(
                "MCP tool 未处理异常",
                context={
                    "phase": "mcp_tool",
                    "operation": name,
                    "fallback": False,
                    "provider_switch_allowed": False,
                    "cause_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc
        return {
            "content": [{"type": "text", "text": _serialize_to_text(data)}],
            "isError": False,
        }


def create_mcp_server(client: Any | None = None, facade: Any | None = None) -> MCPServer:
    return MCPServer(client=client, facade=facade)
