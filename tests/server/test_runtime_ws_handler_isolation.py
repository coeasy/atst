# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P0-6 regression: every WebSocket connection must get its own handler state.

A single shared handler lets ``bind_connection`` overwrite ``_send`` from another
connection (A's push frames land on B's socket).  The built-in handler has always
been cloned; custom subclasses are cloned too now (constructor first, then a shallow
copy with fresh per-connection state).
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from websockets.asyncio.client import connect

from atst.server import runtime_ws as ws_module
from atst.server.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

BOUND = 5.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_builtin_handler_is_isolated_per_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two connections get two built-in handlers with independent send/subscription state."""
    bound: list[Any] = []
    original = ws_module.RuntimeJsonRpcHandler.bind_connection

    def _record(self: Any, loop: Any, send: Any) -> None:
        bound.append(self)
        original(self, loop, send)

    monkeypatch.setattr(ws_module.RuntimeJsonRpcHandler, "bind_connection", _record)
    server = await serve_runtime_ws(config=RuntimeWsConfig(port=_free_port()))
    try:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/v13/ws"
        async with connect(url), connect(url):
            assert len(bound) == 2
    finally:
        server.close()
        await server.wait_closed()

    first, second = bound
    assert first is not second, "two connections share one handler instance"
    assert first._send is not second._send, "second connection overwrote the first _send"
    assert first._subs is not second._subs, "subscription tables are shared across connections"


@pytest.mark.asyncio
async def test_custom_subclass_is_cloned_with_fresh_connection_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subclass with a non-standard constructor still gets a per-connection copy."""

    class TaggedHandler(ws_module.RuntimeJsonRpcHandler):
        def __init__(self, client: Any, tag: str) -> None:
            super().__init__(client=client)
            self.tag = tag

    bound: list[Any] = []
    original = ws_module.RuntimeJsonRpcHandler.bind_connection

    def _record(self: Any, loop: Any, send: Any) -> None:
        bound.append(self)
        original(self, loop, send)

    monkeypatch.setattr(ws_module.RuntimeJsonRpcHandler, "bind_connection", _record)
    handler = TaggedHandler(None, "shared-tag")
    server = await serve_runtime_ws(handler=handler, config=RuntimeWsConfig(port=_free_port()))
    try:
        port = server.sockets[0].getsockname()[1]
        async with (
            connect(f"ws://127.0.0.1:{port}/v13/ws"),
            connect(f"ws://127.0.0.1:{port}/v13/ws"),
        ):
            assert len(bound) == 2
    finally:
        server.close()
        await server.wait_closed()

    first, second = bound
    assert first is not handler and second is not handler, "custom handler was shared"
    assert first is not second
    assert first.tag == second.tag == "shared-tag", "clone lost the custom business state"
    assert first.client is second.client is handler.client, "clones must share the Client"
    assert first._send is not second._send
    assert first._subs is not second._subs
