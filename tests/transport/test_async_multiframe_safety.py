from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from atst.codec.framing import ResponseFrame
from atst.errors import ConfigError, FramingError
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry

async_module = importlib.import_module("atst.transport.async_")


def _frame(payload: bytes, method: int = 0x0530) -> ResponseFrame:
    return ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=0,
        seq=1,
        method=method,
        zip_size=len(payload),
        unzip_size=len(payload),
        body=b"",
        payload=payload,
        header_raw=b"",
    )


class _Writer:
    def write(self, payload: bytes) -> None:
        self.payload = payload

    async def drain(self) -> None:
        return None


class _FakeConn:
    connected = True
    timeout = 1.0
    spec = None

    def __init__(self, first: ResponseFrame, continuation: list[ResponseFrame]) -> None:
        self._lock = asyncio.Lock()
        self._writer = _Writer()
        self.stats = SimpleNamespace(bytes_sent=0, last_used=0.0, created_at=0.0)
        self._first = first
        self._continuation = list(continuation)
        self.closed = False

    def next_seq(self) -> int:
        return 1

    async def _request_locked(
        self,
        method: int,
        body: bytes,
        *,
        timeout: float | None = None,
    ) -> ResponseFrame:
        del method, body, timeout
        return self._first

    async def _read_frame_locked(self) -> ResponseFrame:
        if not self._continuation:
            raise AssertionError("test requested an unexpected extra frame")
        return self._continuation.pop(0)

    async def close(self) -> None:
        self.closed = True
        self.connected = False


def _pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        [HostEntry("127.0.0.1", 7709)],
        slots_per_host=1,
        heartbeat_interval=0,
        handshake=False,
    )


def test_async_request_multi_validates_limits_before_network() -> None:
    async def run() -> None:
        pool = _pool()
        try:
            with pytest.raises(ConfigError, match="max_frames"):
                await pool.request_multi(0x0530, max_frames=0)
            with pytest.raises(ConfigError, match="max_frames"):
                await pool.request_multi(0x0530, max_frames=True)
            with pytest.raises(ConfigError, match="record_size"):
                await pool.request_multi(0x0530, record_size=0)
        finally:
            await pool.close()

    asyncio.run(run())


def test_async_iter_frames_validates_limit_before_network() -> None:
    async def run() -> None:
        pool = _pool()
        try:
            with pytest.raises(ConfigError, match="max_frames"):
                async for _frame_item in pool.iter_frames(0x0530, max_frames=0):
                    raise AssertionError("invalid limit must fail before yielding")
        finally:
            await pool.close()

    asyncio.run(run())


def test_async_request_multi_truncation_marks_failure_and_drops_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = _pool()
        slot = pool._slots[0]
        conn = _FakeConn(
            _frame(b"\x04\x00A"),
            [_frame(b"B"), _frame(b"C"), _frame(b"D")],
        )
        failures: list[BaseException] = []

        async def acquire(_slot: Any) -> tuple[_FakeConn, int]:
            return conn, slot.generation

        async def release(_slot: Any, _conn: Any) -> None:
            return None

        async def mark_failure(
            self: Any,
            failed_slot: Any,
            exc: BaseException,
            *,
            generation: int | None = None,
            conn: Any = None,
        ) -> None:
            del generation
            failures.append(exc)
            await self._drop(failed_slot, expected=conn)

        monkeypatch.setattr(pool, "_acquire_lease", acquire)
        monkeypatch.setattr(pool, "_release_lease", release)
        monkeypatch.setattr(AsyncConnectionPool, "_mark_failure", mark_failure)
        slot.conn = conn  # allow canonical _drop to clear the slot

        try:
            result = await pool.request_multi(
                0x0530,
                record_size=1,
                max_frames=2,
            )
            assert result.payload == b"AB"
            assert len(failures) == 1
            assert isinstance(failures[0], FramingError)
            assert slot.conn is None
            assert conn.closed is True
        finally:
            await pool.close()

    asyncio.run(run())


def test_async_iter_frames_cap_drops_potentially_dirty_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pool = _pool()
        slot = pool._slots[0]
        conn = _FakeConn(
            _frame(b"unused"),
            [_frame(b"A"), _frame(b"B"), _frame(b"C")],
        )

        async def acquire(_slot: Any) -> tuple[_FakeConn, int]:
            return conn, slot.generation

        async def release(_slot: Any, _conn: Any) -> None:
            return None

        async def mark_success(
            self: Any,
            success_slot: Any,
            *,
            generation: int | None = None,
            rtt_ms: float | None = None,
        ) -> None:
            del self, success_slot, generation, rtt_ms

        monkeypatch.setattr(pool, "_acquire_lease", acquire)
        monkeypatch.setattr(pool, "_release_lease", release)
        monkeypatch.setattr(AsyncConnectionPool, "_mark_success", mark_success)
        monkeypatch.setattr(async_module, "build_request", lambda *a, **k: (b"REQ", 1))
        slot.conn = conn

        try:
            payloads = [frame.payload async for frame in pool.iter_frames(0x0530, max_frames=2)]
            assert payloads == [b"A", b"B"]
            assert slot.conn is None
            assert conn.closed is True
            assert len(conn._continuation) == 1
            assert conn._continuation[0].payload == b"C"
        finally:
            await pool.close()

    asyncio.run(run())
