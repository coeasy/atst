# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Thin WebSocket transport for the canonical v13 JSON-RPC handler.

This module owns transport lifecycle only. It contains no market-data business
logic: every request is delegated to :class:`RuntimeJsonRpcHandler`.
"""

from __future__ import annotations

import asyncio
import contextlib
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
    :meth:`RuntimeJsonRpcHandler.close`，那是这条所有权链唯一的落点（``__main__``
    宿主就是这么收尾的）。它释放的是自造内核的收尾责任，不是连接池：每次 JSON-RPC
    取数用的是即用即关的家族客户端，进程里没有跨调用留下的池。
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
        # 每条连接一份 handler：流式订阅表是连接私有的，不能让两条连接串台。
        # 调用方显式传进来的那份仍被所有连接共用（单连接宿主场景），服务器自建的
        # 才真正"每连接一份"——``server.tstdx_handler`` 的所有权口径不变。
        conn_handler = rpc if handler is not None else RuntimeJsonRpcHandler(client=rpc.client)
        # 替身 handler（传输判据用）未必有流式控制面，按能力绑定，避免 AttributeError。
        binder = getattr(conn_handler, "bind_connection", None)
        if binder is not None:
            # 连接处理器需要"这条连接正在跑的事件循环"，才能把推送帧调度到正确的 socket。
            # 用 ``get_running_loop`` 而非 ``get_event_loop``：后者在 3.12 已弃用且可能
            # 造出一个不属于本连接的循环，导致 ``run_coroutine_threadsafe`` 调度错循环、
            # 推送帧石沉大海（第 31 轮 WS 流式判据踩到的坑）。
            binder(asyncio.get_running_loop(), websocket.send)
        try:
            async for raw in websocket:
                # handle_message 是**同步**的：里面是真实 socket 取数 + 结果 json.dumps。
                # 直接在事件循环里调用会拿住整条循环——一个慢请求拖死所有其他连接。
                # HTTP 面不吃这个亏是因为它的路由是同步 def，FastAPI 自动丢线程池；
                # 这里同一口径由 to_thread 补齐（第 26 轮）。逐条 await，
                # 因此同一连接上的应答顺序不变。
                response = await asyncio.to_thread(conn_handler.handle_message, raw)
                if response is not None:
                    # 客户端可能在服务器回帧前就关闭：send 的竞态失败吞掉，不拖垮连接。
                    with contextlib.suppress(Exception):
                        await websocket.send(response)
        finally:
            # 连接断开：停掉本连接拥有的全部实时流，避免 worker 线程/任务泄漏。
            stopper = getattr(conn_handler, "stop_all_subscriptions", None)
            if stopper is not None:
                with contextlib.suppress(Exception):
                    await stopper()

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
