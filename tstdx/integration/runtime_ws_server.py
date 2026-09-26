# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Thin WebSocket transport for the canonical v13 JSON-RPC handler.

This module owns transport lifecycle only. It contains no market-data business
logic: every request is delegated to :class:`RuntimeJsonRpcHandler`.
"""

from __future__ import annotations

import asyncio
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
    """Start the v13 WebSocket server and return the websockets server object.

    生命周期：传入 ``handler`` 时它归调用方，本函数不动它。不传时这里造一份，并把它
    登记在返回的 server 上（``server.tstdx_handler``）——宿主停机时记得
    :meth:`RuntimeJsonRpcHandler.close`，否则那份 ``Client`` 背后的连接池就留在进程里
    了（``__main__`` 宿主就是这么收尾的）。
    """

    try:
        from websockets.asyncio.server import serve
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("WebSocket 服务需要安装可选依赖: pip install tstdx[server]") from exc

    owns_handler = handler is None
    rpc = handler or RuntimeJsonRpcHandler()
    cfg = config or RuntimeWsConfig()
    canonical_path = cfg.path if cfg.path.startswith("/") else f"/{cfg.path}"

    async def _connection(websocket: Any) -> None:
        request_path = getattr(getattr(websocket, "request", None), "path", None)
        if request_path is not None and request_path != canonical_path:
            await websocket.close(code=1008, reason="unsupported path")
            return
        async for raw in websocket:
            # handle_message 是**同步**的：里面是真实 socket 取数 + 结果 json.dumps。
            # 直接在事件循环里调用会拿住整条循环——一个慢请求拖死所有其他连接。
            # HTTP 面不吃这个亏是因为它的路由是同步 def，FastAPI 自动丢线程池；
            # 这里同一口径由 to_thread 补齐（第 26 轮）。逐条 await，
            # 因此同一连接上的应答顺序不变。
            response = await asyncio.to_thread(rpc.handle_message, raw)
            if response is not None:
                await websocket.send(response)

    server = await serve(
        _connection,
        cfg.host,
        cfg.port,
        max_size=cfg.max_size,
    )
    if owns_handler:
        server.tstdx_handler = rpc
    return server


if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import contextlib

    _CFG = RuntimeWsConfig()

    async def _serve_forever() -> None:
        server = await serve_runtime_ws(config=_CFG)
        print(
            f"tstdx v13 WebSocket JSON-RPC 已监听 ws://{_CFG.host}:{_CFG.port}{_CFG.path}",
            flush=True,
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            owned = getattr(server, "tstdx_handler", None)
            if owned is not None:
                with contextlib.suppress(Exception):
                    owned.close()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve_forever())
