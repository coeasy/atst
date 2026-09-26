"""异步停机路径的等待必须有上界（第 28 轮 28-B-1）。

抓到的形状：``AsyncTcpConnection.close()`` 里那句裸 ``await writer.wait_closed()``。它等的是
asyncio 协议的 ``connection_lost`` 回调 —— 而 ``writer.close()`` 之后那个回调**不一定到来**
（selector 传输要先排空发送缓冲，对端不再读取时它一直挂着）。落点有两处要命的地方：

一、:meth:`AsyncConnectionPool.close` 经 ``_cleanup_committed_close`` **逐个槽位**排空，
   所以"关不掉"的代价按槽位数乘上去；
二、读帧失败路径 ``_recv_exact`` 是 ``await self.close()`` 之后才抛
   :class:`~tstdx.errors.ConnectionClosed` —— 读超时的墙钟已经到点，调用方却仍然回不来。

下面的用例按这两条各钉一格：挂死的 writer 必须在上界内被放手（并且不吞掉取消），
健康的 writer 不被上界拖慢；池关停的总耗时按 ``槽位数 × CLOSE_WAIT_SECONDS`` 收口。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tstdx.transport.async_ import CLOSE_WAIT_SECONDS, AsyncConnectionPool, AsyncTcpConnection
from tstdx.transport.hosts import HostEntry


class _Writer:
    """最小的 StreamWriter 替身：只实现 ``close`` 路径会用到的三个方法。"""

    def __init__(self, *, stuck: bool) -> None:
        self.closed_calls = 0
        self.wait_calls = 0
        self._stuck = stuck
        self._release = asyncio.Event()

    def close(self) -> None:
        self.closed_calls += 1

    def is_closing(self) -> bool:
        return self.closed_calls > 0

    async def wait_closed(self) -> None:
        self.wait_calls += 1
        if self._stuck:
            await self._release.wait()


def _conn(*, stuck: bool) -> tuple[AsyncTcpConnection, _Writer]:
    conn = AsyncTcpConnection("127.0.0.1", 7709, timeout=0.2, handshake=False)
    writer = _Writer(stuck=stuck)
    conn._reader = asyncio.StreamReader()
    conn._writer = writer
    return conn, writer


def test_stalled_transport_is_released_within_the_close_bound() -> None:
    async def run() -> None:
        conn, writer = _conn(stuck=True)
        started = time.monotonic()
        await asyncio.wait_for(conn.close(), timeout=CLOSE_WAIT_SECONDS * 4)
        elapsed = time.monotonic() - started

        assert writer.closed_calls == 1, "上界不能替代真正的 close"
        assert writer.wait_calls == 1
        assert CLOSE_WAIT_SECONDS <= elapsed < CLOSE_WAIT_SECONDS * 3, elapsed

    asyncio.run(run())


def test_prompt_transport_is_not_slowed_by_the_bound() -> None:
    async def run() -> None:
        conn, writer = _conn(stuck=False)
        started = time.monotonic()
        await conn.close()
        elapsed = time.monotonic() - started

        assert writer.wait_calls == 1
        assert elapsed < CLOSE_WAIT_SECONDS / 2, elapsed
        assert conn._writer is None

    asyncio.run(run())


def test_the_close_bound_is_still_cancellable() -> None:
    """上界是兜底，不是把关停变成不可取消的阻塞——取消必须当场传播。"""

    async def run() -> None:
        conn, writer = _conn(stuck=True)
        task = asyncio.create_task(conn.close())
        await asyncio.sleep(0)
        assert writer.closed_calls == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_pool_close_bounds_total_teardown_by_slot_count() -> None:
    async def run() -> None:
        pool = AsyncConnectionPool(
            [HostEntry("127.0.0.1", 7709), HostEntry("127.0.0.2", 7709)],
            slots_per_host=1,
            heartbeat_interval=0,
            handshake=False,
        )
        writers = [_Writer(stuck=True) for _ in pool._slots]
        for slot, writer in zip(pool._slots, writers, strict=True):
            conn = AsyncTcpConnection("127.0.0.1", 7709, timeout=0.2, handshake=False)
            conn._reader = asyncio.StreamReader()
            conn._writer = writer
            slot.conn = conn

        started = time.monotonic()
        await pool.close()
        elapsed = time.monotonic() - started

        assert [w.closed_calls for w in writers] == [1, 1], "每条连接都被排空"
        assert all(slot.conn is None for slot in pool._slots)
        assert elapsed < len(writers) * CLOSE_WAIT_SECONDS + 0.5, elapsed

    asyncio.run(run())
