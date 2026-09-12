# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Thin WebSocket transport for the canonical v13 JSON-RPC handler.

This module owns transport lifecycle only. It contains no market-data business
logic: every request is delegated to :class:`RuntimeJsonRpcHandler`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runtime_ws import RuntimeJsonRpcHandler

__all__ = ["RuntimeWsConfig", "serve_runtime_ws"]


@dataclass(frozen=True, slots=True)
class RuntimeWsConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    path: str = "/v13/ws"
    max_size: int = 1_048_576


async def serve_runtime_ws(
    *,
    handler: RuntimeJsonRpcHandler | None = None,
    config: RuntimeWsConfig | None = None,
) -> Any:
    """Start the v13 WebSocket server and return the websockets server object."""

    try:
        from websockets.asyncio.server import serve
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("WebSocket 服务需要安装可选依赖: pip install tstdx[server]") from exc

    rpc = handler or RuntimeJsonRpcHandler()
    cfg = config or RuntimeWsConfig()
    canonical_path = cfg.path if cfg.path.startswith("/") else f"/{cfg.path}"

    async def _connection(websocket: Any) -> None:
        request_path = getattr(getattr(websocket, "request", None), "path", None)
        if request_path is not None and request_path != canonical_path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        async for raw in websocket:
            response = rpc.handle_message(raw)
            if response is not None:
                await websocket.send(response)

    return await serve(
        _connection,
        cfg.host,
        cfg.port,
        max_size=cfg.max_size,
    )
