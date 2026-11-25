# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Regression: ``AsyncTcpConnection.next_seq()`` is only called under ``conn._lock``.

The counter increment is a plain synchronous ``self._seq = (self._seq + 1) & 0xFFFFFFFF``.
Its atomicity therefore relies entirely on every call site running while the
connection's ``asyncio.Lock`` is held.  Within one event loop the lock serialises
those call sites (and the increment has no ``await`` of its own), so there is no
coroutine race; the connection owns loop-bound stream I/O and is never shared
across event loops / threads.  These tests pin that invariant at runtime with a
spy that fails if ``next_seq()`` is ever entered without the lock, and verify
that a concurrent same-loop burst still yields unique, consecutive sequences.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from atst.transport.async_ import AsyncConnectionPool, AsyncTcpConnection
from atst.transport.hosts import HostEntry

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import (  # noqa: E402
    ECHO_CMD,
    MULTI_CMD,
    FakeTdxServer,
    multi_body,
)


def _spy_next_seq(monkeypatch: Any) -> list[int]:
    """Record every seq value, asserting the lock is held at each invocation."""
    values: list[int] = []
    original = AsyncTcpConnection.next_seq

    def spy(self: AsyncTcpConnection) -> int:
        assert self._lock.locked(), "next_seq() called without conn._lock held"
        value = original(self)
        values.append(value)
        return value

    monkeypatch.setattr(AsyncTcpConnection, "next_seq", spy)
    return values


def _make_pool(server: FakeTdxServer) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        [HostEntry("127.0.0.1", server.port)],
        slots_per_host=1,
        timeout=5.0,
        heartbeat_interval=0,
        handshake=False,
    )


def test_next_seq_only_called_while_connection_lock_held(monkeypatch: Any) -> None:
    """All four seq call sites (request / ping / request_multi / iter_frames) hold the lock.

    The spy asserts ``_lock.locked()`` on every call, so an escaped call site
    fails immediately even though we exercise the real request paths.
    """
    values = _spy_next_seq(monkeypatch)

    with FakeTdxServer() as server:

        async def run() -> None:
            # Direct connection: request() and ping() call sites.
            conn = AsyncTcpConnection("127.0.0.1", server.port, handshake=False)
            await conn.connect()
            await conn.request(ECHO_CMD, b"a")
            await conn.ping()
            await conn.close()

            # Pool paths: _request_locked via request/request_multi, iter_frames.
            pool = _make_pool(server)
            await pool.request(ECHO_CMD, b"b")
            await pool.request_multi(MULTI_CMD, multi_body(5, 4, 6), record_size=4)
            async for _frame in pool.iter_frames(MULTI_CMD, multi_body(5, 4, 6), max_frames=64):
                pass
            await pool.close()

        asyncio.run(run())

    # request + ping on the direct connection (seq 1, 2), then request +
    # request_multi + iter_frames on a fresh pooled connection (seq 1, 2, 3).
    assert values == [1, 2, 1, 2, 3]


def test_concurrent_same_loop_requests_yield_unique_sequences(monkeypatch: Any) -> None:
    """Concurrent requests in one loop never collide: the lock serialises the increment."""
    values = _spy_next_seq(monkeypatch)

    with FakeTdxServer() as server:

        async def run() -> None:
            pool = _make_pool(server)
            await asyncio.gather(*(pool.request(ECHO_CMD, b"x") for _ in range(16)))
            await pool.close()

        asyncio.run(run())

    assert len(values) == 16
    assert len(set(values)) == 16
    # Consecutive modulo 2**32 (no wrap at this magnitude).
    assert sorted(values) == list(range(min(values), min(values) + 16))
