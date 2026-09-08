# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Official protocol edges over the canonical v12 runtime.

Package-level factories are the supported integration entrypoints. REST uses the
planned Provider service and bounded TaskManager v2; WebSocket and MCP use the
same canonical ErrorEnvelope. Historical implementation modules remain importable
for compatibility and test injection, but new applications should import from
``tstdx.integration``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "create_app",
    "create_mcp_server",
    "serve_ws",
    "JsonRpcHandler",
    "WsConfig",
]


def create_app(*args: Any, **kwargs: Any) -> Any:
    from .http_app import create_app as _create_app

    return _create_app(*args, **kwargs)


def create_mcp_server(*args: Any, **kwargs: Any) -> Any:
    from .mcp_app import create_mcp_server as _create_mcp_server

    return _create_mcp_server(*args, **kwargs)


def serve_ws(*args: Any, **kwargs: Any) -> Any:
    from .ws_app import serve_ws as _serve_ws

    return _serve_ws(*args, **kwargs)


def __getattr__(name: str) -> Any:
    if name in {"JsonRpcHandler", "WsConfig"}:
        from .ws_app import JsonRpcHandler, WsConfig

        return {"JsonRpcHandler": JsonRpcHandler, "WsConfig": WsConfig}[name]
    raise AttributeError(name)
