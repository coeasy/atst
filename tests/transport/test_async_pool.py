# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""异步传输回归：C5 ping 互斥 / C6 续帧锁与弃连 / T5 心跳旧引用与 get_running_loop。

使用 ``asyncio.run`` 驱动（不依赖 pytest-asyncio 配置）。罐头 server 复用
``tests/transport/fake_server.py``。响应 payload 布局 = seq(4LE) + body（回显）。
"""

from __future__ import annotations

import asyncio
import struct
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tstdx.errors import ConnectionClosed
from tstdx.transport.async_ import AsyncConnectionPool, AsyncTcpConnection
from tstdx.transport.hosts import HostEntry

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import (  # noqa: E402
    ECHO_CMD,
    HB_CMD,
    MULTI_CMD,
    FakeTdxServer,
    multi_blob,
    multi_body,
)


def _make_pool(server: FakeTdxServer, **kw) -> AsyncConnectionPool:
    defaults = dict(slots_per_host=1, timeout=5.0, heartbeat_interval=0, handshake=False)
    defaults.update(kw)
    return AsyncConnectionPool([HostEntry("127.0.0.1", server.port)], **defaults)


class _FakeWriter:
    """供心跳确定性测试用的假 StreamWriter。"""

    def __init__(self, drain_sleep: float = 0.0) -> None:
        self.drain_sleep = drain_sleep

    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:  # noqa: ARG002
        pass

    async def drain(self) -> None:
        if self.drain_sleep:
            await asyncio.sleep(self.drain_sleep)

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# C5：async ping 与 request 互斥
# --------------------------------------------------------------------------- #
def test_async_ping_and_request_are_serialized():
    """并发 request + ping：双方都成功、响应帧不被窃取、server 无错帧。"""

    async def main() -> tuple[Any, float, list[int], list[str]]:
        with FakeTdxServer() as server:
            server.delay[ECHO_CMD] = 0.4  # 在飞请求期间给 ping 制造插入窗口
            conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
            await conn.connect()
            try:
                frame, rtt = await asyncio.gather(
                    conn.request(ECHO_CMD, b"\xaa\xbb"),
                    conn.ping(),
                )
            finally:
                await conn.close()
            record = server.connections[0]
            return frame, rtt, list(record.frames), record.misframed

    frame, rtt, frames, misframed = asyncio.run(main())
    assert frame.payload[4:] == b"\xaa\xbb"  # 回显 body 完整（未被 ping 帧污染）
    assert rtt > 0
    assert misframed == []
    assert [m for m, _s, _b in frames] and set(m for m, _s, _b in frames) == {
        ECHO_CMD,
        HB_CMD,
    }
    # 无锁实现下 ping 会读走 request 的响应帧（ping 不校验 seq → 静默窃取），
    # 随后 request 的 check_seq 必炸；双成功即互斥证据。
    assert frame.seq >= 1


def test_async_ping_and_request_serialized_many_rounds():
    """20 轮并发放大：request/ping 全部成功、server 零错帧。"""

    async def main() -> None:
        with FakeTdxServer() as server:
            server.delay[ECHO_CMD] = 0.02
            conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
            await conn.connect()
            try:
                for i in range(20):
                    frame, rtt = await asyncio.gather(
                        conn.request(ECHO_CMD, bytes([i])),
                        conn.ping(),
                    )
                    assert frame.payload[4:] == bytes([i])
                    assert rtt > 0
            finally:
                await conn.close()
            assert server.all_misframed() == []

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# C6：request_multi 续帧纳入 conn 锁 + TdxError 弃连
# --------------------------------------------------------------------------- #
def test_async_request_multi_merges_frames():
    async def main() -> bytes:
        with FakeTdxServer() as server:
            async with _make_pool(server) as pool:
                merged = await pool.request_multi(MULTI_CMD, multi_body(5, 4, 6), record_size=4)
                assert server.all_misframed() == []
                return merged.payload

    assert asyncio.run(main()) == multi_blob(5, 4)


def test_async_request_multi_drops_conn_on_continuation_error():
    """续帧中断 → 弃连（对齐同步 _mark_failure → _drop），不再残留半包。"""

    async def main() -> tuple[bytes, object]:
        with FakeTdxServer() as server:
            server.close_after.add(MULTI_CMD)
            pool = _make_pool(server)
            try:
                merged = await pool.request_multi(MULTI_CMD, multi_body(5, 4, 6), record_size=4)
                slot_conn = pool._slots[0].conn
            finally:
                await pool.close()
            return merged.payload, slot_conn

    payload, slot_conn = asyncio.run(main())
    # 异步 request_multi 保持既有语义：降级返回已合并部分（首帧 chunk），
    # 不做同步版的「payload 清空」（同步/异步该差异为既有行为，保持最小变更）。
    assert payload == multi_blob(5, 4)[:6]
    assert slot_conn is None, "续帧中断必须弃连"


def test_async_dropped_conn_does_not_poison_next_request():
    """半包场景：弃连后同池下一个请求正常（新连接上无残留帧）。"""

    async def main() -> tuple[bytes, bytes]:
        with FakeTdxServer() as server:
            server.close_after.add(MULTI_CMD)
            async with _make_pool(server) as pool:
                first = await pool.request_multi(MULTI_CMD, multi_body(5, 4, 6), record_size=4)
                server.close_after.clear()
                frame = await pool.request(ECHO_CMD, b"\x01\x02")
                return first.payload, frame.payload

    first_payload, echo_payload = asyncio.run(main())
    assert first_payload == multi_blob(5, 4)[:6]  # 降级返回部分数据
    assert echo_payload[4:] == b"\x01\x02"


# --------------------------------------------------------------------------- #
# C6：iter_frames —— write+drain 超时 / TdxError 弃连 / _closed 复查
# --------------------------------------------------------------------------- #
def test_async_iter_frames_drops_conn_when_caller_cap_reached():
    """读满调用方上限 ≠ 服务端流已结束：socket 可能残留未读帧，必须弃连。

    ``_async_pool_hardening`` 对齐了同步的 multiframe 截断安全契约：到达
    ``max_frames`` 上限即弃连（且不计为主站失败），以免下一次请求读到本请求
    的旧帧。
    """

    async def main() -> tuple[list[bytes], object, int]:
        with FakeTdxServer() as server:
            async with _make_pool(server) as pool:
                frames = [
                    f.payload
                    async for f in pool.iter_frames(MULTI_CMD, multi_body(5, 4, 6), max_frames=4)
                ]
                slot = pool._slots[0]
                return frames, slot.conn, slot.host.failures

    frames, slot_conn, host_failures = asyncio.run(main())
    assert len(frames) == 4
    assert frames[0] == struct.pack("<H", 5) + multi_blob(5, 4)[:6]
    assert slot_conn is None, "到达上限后 socket 可能仍有未读帧，必须弃连"
    assert host_failures == 0, "读满上限是干净结束，不得计入主站失败"


def test_async_iter_frames_drops_conn_on_truncated_stream():
    async def main() -> tuple[list[bytes], object]:
        with FakeTdxServer() as server:
            server.close_after.add(MULTI_CMD)
            async with _make_pool(server) as pool:
                got: list[bytes] = []
                async for f in pool.iter_frames(MULTI_CMD, multi_body(5, 4, 6), max_frames=64):
                    got.append(f.payload)
                # 流被截断：迭代以「读到首帧后干净结束」收尾（与同步一致不抛），
                # 但连接必须已弃置
                return got, pool._slots[0].conn

    got, slot_conn = asyncio.run(main())
    assert len(got) >= 1  # 至少读到首帧
    assert slot_conn is None, "TdxError 后必须弃连（对齐同步 iter_frames _mark_failure）"


def test_async_pool_ensure_open_on_all_paths():
    async def main() -> None:
        with FakeTdxServer() as server:
            pool = _make_pool(server)
            await pool.close()
            with pytest.raises(ConnectionClosed):
                await pool.request(ECHO_CMD, b"x")
            with pytest.raises(ConnectionClosed):
                await pool.request_multi(MULTI_CMD, multi_body(1, 4, 4))
            with pytest.raises(ConnectionClosed):
                async for _f in pool.iter_frames(MULTI_CMD, multi_body(1, 4, 4)):
                    pass

    asyncio.run(main())


# --------------------------------------------------------------------------- #
# T5：心跳持旧 conn 引用不误杀新连接 / get_running_loop
# --------------------------------------------------------------------------- #
def test_async_heartbeat_does_not_kill_rebuilt_conn():
    """ping 挂起期间连接被弃连重建：心跳失败只弃自己探测的旧连接。"""

    async def main() -> object:
        old_conn = AsyncTcpConnection("10.255.255.1", 1, timeout=10.0, handshake=False)
        old_conn._writer = _FakeWriter(drain_sleep=2.0)  # ping 挂起 2s 制造窗口
        old_conn.stats.created_at = time.time() - 10  # idle 足够触发心跳
        old_conn.stats.last_used = 0.0

        new_conn = AsyncTcpConnection("127.0.0.1", 1, timeout=5.0, handshake=False)
        new_conn._writer = _FakeWriter()
        new_conn.stats.last_used = time.time()  # 新连接刚被使用 → 心跳跳过它

        pool = AsyncConnectionPool(
            [HostEntry("127.0.0.1", 1)], slots_per_host=1, heartbeat_interval=0, handshake=False
        )
        pool._slots[0].conn = old_conn
        pool._hb = asyncio.get_running_loop().create_task(pool._heartbeat_loop())
        try:
            await asyncio.sleep(1.3)  # t≈1.0 心跳捕获 old_conn 并进入 ping
            pool._slots[0].conn = new_conn  # 模拟请求路径弃连重建
            await asyncio.sleep(2.5)  # t≈3.0 旧连接的 ping 失败 → guard 生效
            return pool._slots[0].conn
        finally:
            await pool.close()

    slot_conn = asyncio.run(main())
    assert slot_conn is not None, "心跳失败不得把重建后的新连接一起弃掉"


def test_async_start_heartbeat_uses_running_loop():
    """get_running_loop 替换 get_event_loop：运行中循环内可直接启动。"""

    async def main() -> bool:
        with FakeTdxServer() as server:
            async with _make_pool(server, heartbeat_interval=1) as pool:
                pool.start_heartbeat()  # 旧实现此处 DeprecationWarning / 3.12+ 异常
                frame = await pool.request(ECHO_CMD, b"\x07")
                return frame.payload[4:] == b"\x07"

    assert asyncio.run(main())
