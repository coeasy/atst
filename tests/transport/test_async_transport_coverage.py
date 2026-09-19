# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""异步传输层回归（v17 Phase 5 覆盖率收口）。

``tstdx/transport/async_.py`` 此前只有 C5/C6/T5 三个并发语义专项测试，
连接生命周期、错误归类、池请求主流程、bestip 热更新、多帧与心跳循环
均无覆盖。本文件用 :mod:`tests.transport.fake_server` 的罐头主站 +
一个可控的「罐头 asyncio server」把这些路径逐条打通（全离线 loopback）。

约定：所有驱动均用 ``asyncio.run``，不依赖 pytest-asyncio 配置。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tstdx.codec.framing import DEFAULT_7709_SPEC
from tstdx.errors import (
    AllHostsUnreachable,
    ConfigError,
    ConnectionClosed,
    ConnectionFailed,
    FramingError,
    ProtocolError,
    RateLimitedLocal,
    ReadTimeout,
    TdxError,
)
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool, AsyncTcpConnection
from tstdx.transport.hosts import HostEntry, next_generation_host
from tstdx.transport.ratelimit import SessionRateLimiter

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import (  # noqa: E402
    ECHO_CMD,
    MAGIC,
    MULTI_CMD,
    REQ_HEADER,
    RESP_HEADER,
    FakeTdxServer,
    multi_blob,
    multi_body,
)

pytestmark = pytest.mark.unit


def run(coro: Any) -> Any:
    return asyncio.run(coro)


Responder = Callable[[int, int, bytes], Awaitable[bytes | None]]


def _frame(seq: int, method: int, payload: bytes = b"", *, magic: int = MAGIC) -> bytes:
    return RESP_HEADER.pack(magic, 0, seq, 0, method, len(payload), len(payload)) + payload


class _HangupAfter(bytes):
    """回包写完后立即断开：制造「头已读、体不足」的半包 / EOF 场景。"""


class CannedServer:
    """单连接 asyncio 罐头服务：按 ``responder`` 的字节回包，用于错帧/超时路径。

    ``responder(method, seq, body) -> bytes | None``；返回 ``None`` 表示立即断开，
    返回 :class:`_HangupAfter` 表示写完该帧再断开。
    """

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.requests: list[tuple[int, int, bytes]] = []
        self._server: asyncio.Server | None = None
        self.port = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(REQ_HEADER.size)
                _zip, seq, _ptype, pkg1, _pkg2, method = REQ_HEADER.unpack(header)
                body = await reader.readexactly(max(0, pkg1 - 2))
                self.requests.append((method, seq, body))
                reply = await self.responder(method, seq, body)
                if reply is None:
                    break
                writer.write(reply)
                await writer.drain()
                if isinstance(reply, _HangupAfter):
                    break
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

    async def __aenter__(self) -> CannedServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()


def _closed_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


class _FakeWriter:
    """替换 ``conn._writer``：按脚本让 drain / write 失败。"""

    def __init__(
        self,
        *,
        drain_sleep: float = 0.0,
        write_exc: BaseException | None = None,
    ) -> None:
        self.drain_sleep = drain_sleep
        self.write_exc = write_exc
        self.closed = False

    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        if self.write_exc is not None:
            raise self.write_exc

    async def drain(self) -> None:
        if self.drain_sleep:
            await asyncio.sleep(self.drain_sleep)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeLimiter:
    """``SessionRateLimiter`` 替身：可控 strict / try_acquire 行为。"""

    def __init__(self, *, strict: bool, allow_first: bool = True) -> None:
        self.strict = strict
        self.allow_first = allow_first
        self.acquire_calls = 0
        self.try_calls = 0

    def acquire(self) -> None:
        self.acquire_calls += 1
        raise RateLimitedLocal("限流：strict 模式拒绝")

    def try_acquire(self) -> bool:
        self.try_calls += 1
        return self.try_calls > 1 if not self.allow_first else self.try_calls == 1


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
class TestAsyncConnectionLifecycle:
    def test_connect_request_and_reconnect_shortcut(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
                await conn.connect()
                assert conn.connected
                assert conn.addr == ("127.0.0.1", server.port)
                frame = await conn.request(ECHO_CMD, b"abc")
                assert frame.payload[4:] == b"abc"
                # 已连接时 connect 直接短路，不再建第二条 socket
                assert await conn.connect() is conn
                assert conn.stats.requests == 1
                health = conn.health()
                assert health["connected"] is True and health["handshaked"] is False
                await conn.close()
                assert not conn.connected
                await conn.close()  # 幂等

            run(go())

    def test_context_manager_closes(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                async with AsyncTcpConnection(
                    "127.0.0.1", server.port, timeout=5.0, handshake=False
                ) as conn:
                    assert conn.connected
                assert not conn.connected

            run(go())

    def test_handshake_sets_flag(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=True)
                await conn.connect()
                assert conn.health()["handshaked"] is True
                # 握手帧已消费：后续业务请求仍对齐
                frame = await conn.request(ECHO_CMD, b"x")
                assert frame.payload[4:] == b"x"

            run(go())

    def test_connection_refused_maps_to_connection_failed(self) -> None:
        async def go() -> None:
            conn = AsyncTcpConnection(
                "127.0.0.1", _closed_port(), timeout=0.5, connect_timeout=0.5, handshake=False
            )
            with pytest.raises(ConnectionFailed):
                await conn.connect()

        run(go())

    def test_connect_timeout_maps_to_connection_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def slow(*_a: Any, **_kw: Any) -> None:
            await asyncio.sleep(5)

        monkeypatch.setattr(asyncio, "open_connection", slow)

        async def go() -> None:
            conn = AsyncTcpConnection("127.0.0.1", 9, timeout=1.0, connect_timeout=0.05)
            with pytest.raises(ConnectionFailed) as exc:
                await conn.connect()
            assert "超时" in str(exc.value)

        run(go())

    def test_next_seq_wraps_at_uint32(self) -> None:
        conn = AsyncTcpConnection("127.0.0.1", 7709)
        conn._seq = 0xFFFFFFFF
        assert conn.next_seq() == 0
        assert conn.next_seq() == 1

    def test_require_reader_after_close(self) -> None:
        conn = AsyncTcpConnection("127.0.0.1", 7709)
        with pytest.raises(ConnectionClosed):
            conn._require_reader()

    def test_magic_mismatch_raises_framing_error(self) -> None:
        async def responder(method: int, seq: int, _body: bytes) -> bytes | None:
            return _frame(seq, method, magic=0xDEADBEEF)

        async def go() -> None:
            async with CannedServer(responder) as canned:
                conn = AsyncTcpConnection("127.0.0.1", canned.port, timeout=5.0, handshake=False)
                with pytest.raises(FramingError):
                    await conn.request(ECHO_CMD, b"")

        run(go())

    def test_oversized_frame_rejected_before_body_read(self) -> None:
        async def responder(method: int, seq: int, _body: bytes) -> bytes | None:
            huge = DEFAULT_7709_SPEC.max_frame_bytes + 16
            return RESP_HEADER.pack(MAGIC, 0, seq, 0, method, huge, huge)

        async def go() -> None:
            async with CannedServer(responder) as canned:
                conn = AsyncTcpConnection("127.0.0.1", canned.port, timeout=5.0, handshake=False)
                with pytest.raises(FramingError, match="过大"):
                    await conn.request(ECHO_CMD, b"")
                # 出错后连接被丢弃，不留半包
                assert not conn.connected

        run(go())

    def test_seq_mismatch_raises_protocol_error(self) -> None:
        async def responder(method: int, seq: int, _body: bytes) -> bytes | None:
            return _frame(seq + 100, method, b"payload")

        async def go() -> None:
            async with CannedServer(responder) as canned:
                conn = AsyncTcpConnection("127.0.0.1", canned.port, timeout=5.0, handshake=False)
                with pytest.raises(ProtocolError, match="seq"):
                    await conn.request(ECHO_CMD, b"")
                assert not conn.connected
                # check_seq=False 允许调用方自担错位风险（批帧续读语义）
                conn2 = AsyncTcpConnection("127.0.0.1", canned.port, timeout=5.0, handshake=False)
                frame = await conn2.request(ECHO_CMD, b"", check_seq=False)
                assert frame.payload == b"payload"
                await conn2.close()

        run(go())

    def test_read_frame_public_entry(self) -> None:
        async def responder(method: int, seq: int, _body: bytes) -> bytes | None:
            # 一次回两帧：第一帧由 request 消费，第二帧由 read_frame 取
            return _frame(seq, method, b"first") + _frame(seq, method, b"second")

        async def go() -> None:
            async with CannedServer(responder) as canned:
                conn = AsyncTcpConnection("127.0.0.1", canned.port, timeout=5.0, handshake=False)
                first = await conn.request(ECHO_CMD, b"")
                assert first.payload == b"first"
                second = await conn.read_frame()
                assert second.payload == b"second"
                await conn.close()

        run(go())

    def test_peer_close_maps_to_connection_closed(self) -> None:
        async def responder(method: int, seq: int, _body: bytes) -> bytes | None:
            return _HangupAfter(RESP_HEADER.pack(MAGIC, 0, seq, 0, method, 64, 64))

        async def go() -> None:
            async with CannedServer(responder) as canned:
                conn = AsyncTcpConnection("127.0.0.1", canned.port, timeout=2.0, handshake=False)
                with pytest.raises(ConnectionClosed):
                    await conn.request(ECHO_CMD, b"")

        run(go())

    def test_read_timeout_and_stats(self) -> None:
        with FakeTdxServer() as server:
            server.silent.add(ECHO_CMD)

            async def go() -> None:
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=0.1, handshake=False)
                await conn.connect()
                with pytest.raises(ReadTimeout):
                    await conn.request(ECHO_CMD, b"")
                assert conn.stats.failures == 1
                assert "ReadTimeout" in conn.stats.last_error
                assert not conn.connected

            run(go())

    def test_write_timeout_uses_request_timeout_override(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
                await conn.connect()
                conn._reader = asyncio.StreamReader()
                conn._writer = _FakeWriter(drain_sleep=1.0)  # type: ignore[assignment]
                with pytest.raises(Exception) as exc:
                    await conn.request(ECHO_CMD, b"", timeout=0.05)
                assert type(exc.value).__name__ == "WriteTimeout"
                # 请求级 timeout 不泄漏到连接
                assert conn.timeout == 5.0

            run(go())

    def test_write_oserror_maps_to_connection_closed(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
                await conn.connect()
                conn._reader = asyncio.StreamReader()
                conn._writer = _FakeWriter(write_exc=OSError("broken pipe"))  # type: ignore[assignment]
                with pytest.raises(ConnectionClosed):
                    await conn.request(ECHO_CMD, b"")

            run(go())

    def test_handshake_failure_paths(self) -> None:
        with FakeTdxServer() as server:
            # 握手帧发出即断连
            server.close_on.update({0x000D, 0x0FDB})

            async def go() -> None:
                loose = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0)
                await loose.connect()
                assert loose.health()["handshaked"] is False
                assert "handshake" in loose.stats.last_error

                strict = AsyncTcpConnection(
                    "127.0.0.1", server.port, timeout=5.0, handshake_strict=True
                )
                with pytest.raises(TdxError):
                    await strict.connect()
                assert not strict.connected

            run(go())


# --------------------------------------------------------------------------- #
# 池：请求主流程
# --------------------------------------------------------------------------- #
def _pool(server: FakeTdxServer, **kw: Any) -> AsyncConnectionPool:
    defaults: dict[str, Any] = {
        "slots_per_host": 1,
        "timeout": 5.0,
        "heartbeat_interval": 0,
        "handshake": False,
    }
    defaults.update(kw)
    return AsyncConnectionPool([HostEntry("127.0.0.1", server.port)], **defaults)


class TestAsyncPoolRequest:
    def test_success_updates_host_health(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                frame = await pool.request(ECHO_CMD, b"hi")
                assert frame.payload[4:] == b"hi"
                host = pool.hosts[0]
                assert host.failures == 0
                assert host.live_ok_at and host.live_rtt_ms is not None
                health = pool.health()
                assert health["family"] == Family.STANDARD
                assert health["slots"][0]["uses"] == 1
                assert health["slots"][0]["connected"] is True
                await pool.close()
                assert pool._closed
                with pytest.raises(ConnectionClosed):
                    await pool.request(ECHO_CMD, b"")

            run(go())

    def test_round_robin_over_slots(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, slots_per_host=3)
                keys = [pool._ordered()[0].key for _ in range(4)]
                assert len(set(keys)) == 3  # 轮转覆盖全部槽位后回到起点
                await pool.close()

            run(go())

    def test_empty_slot_list_breaks_and_raises(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                pool._slots = []
                with pytest.raises(AllHostsUnreachable) as exc:
                    await pool.request(ECHO_CMD, b"")
                assert exc.value.context["hosts"] == []
                assert pool._ordered() == []

            run(go())

    def test_unreachable_hosts_retries_then_raises(self) -> None:
        async def go() -> None:
            first, second = _closed_port(), _closed_port()
            while second == first:
                second = _closed_port()
            dead_a = HostEntry("127.0.0.1", first)
            dead_b = HostEntry("127.0.0.1", second)
            pool = AsyncConnectionPool([dead_a, dead_b], max_retries=2)
            with pytest.raises(AllHostsUnreachable) as exc:
                await pool.request(ECHO_CMD, b"")
            # 每轮换主站：两个站点都被试过
            assert set(exc.value.context["hosts"]) == {dead_a.key, dead_b.key}
            assert dead_a.failures + dead_b.failures == 0  # 不回写调用方对象
            assert sum(h.failures for h in pool.hosts) >= 1  # 记账在池自有快照
            assert any(h.last_error for h in pool.hosts)
            await pool.close()

        run(go())

    def test_non_tdx_error_path(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, max_retries=1)
                calls = {"n": 0}
                original = AsyncTcpConnection.request

                async def flaky(self: AsyncTcpConnection, *a: Any, **kw: Any) -> Any:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise RuntimeError("kernel panic")
                    return await original(self, *a, **kw)

                AsyncTcpConnection.request = flaky  # type: ignore[method-assign]
                try:
                    frame = await pool.request(ECHO_CMD, b"x")
                    assert frame.payload[4:] == b"x"
                finally:
                    AsyncTcpConnection.request = original  # type: ignore[method-assign]
                await pool.close()

            run(go())

    def test_strict_limiter_rejects(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                limiter = _FakeLimiter(strict=True)
                pool = _pool(server, rate_limiter=limiter)
                with pytest.raises(RateLimitedLocal):
                    await pool.request(ECHO_CMD, b"")
                assert limiter.acquire_calls == 1
                await pool.close()

            run(go())

    def test_non_strict_limiter_waits_for_token(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                limiter = _FakeLimiter(strict=False, allow_first=False)
                pool = _pool(server, rate_limiter=limiter)
                frame = await pool.request(ECHO_CMD, b"x")
                assert frame.payload[4:] == b"x"
                assert limiter.try_calls == 2  # 首轮无令牌 → 异步等待后放行
                await pool.close()

            run(go())

    def test_spec_injection_and_retired_slot(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, spec=DEFAULT_7709_SPEC)
                slot = pool._slots[0]
                await pool.request(ECHO_CMD, b"x")
                assert slot.conn is not None
                assert slot.conn.spec is DEFAULT_7709_SPEC
                slot.retired = True
                with pytest.raises(ConnectionClosed):
                    await pool._get_conn(slot)
                await pool.close()

            run(go())


class TestAsyncPoolUpdateHosts:
    def test_update_hosts_keeps_live_connection(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                old = HostEntry("127.0.0.1", server.port, rtt_ms=50.0)
                pool = AsyncConnectionPool([old], slots_per_host=1, handshake=False)
                await pool.request(ECHO_CMD, b"x")
                conn_before = pool._slots[0].conn
                assert conn_before is not None

                # bestip 热更新：同一 key 的新 entry 复用连接并继承运行期健康。
                # 池发布的是自己持有的快照（provenance 硬化层），不共享调用方对象。
                refreshed = HostEntry("127.0.0.1", server.port, rtt_ms=9.0)
                refreshed.failures = 1
                hosts = await pool.update_hosts([refreshed])
                assert [h.key for h in hosts] == [refreshed.key]
                assert hosts[0] is not refreshed
                assert pool.hosts == hosts
                assert pool._slots[0].conn is conn_before
                assert pool._slots[0].generation == 1
                assert hosts[0].rtt_ms == 9.0  # 采纳新测速值
                assert hosts[0].live_rtt_ms is not None  # 继承运行期实测
                assert hosts[0].failures == 0  # 运行期失败计数不被外部对象污染

                # 移除的主站：槽位退役、连接被丢弃；端点身份沿用上一代快照
                other = HostEntry("127.0.0.1", server.port, name="only")
                await pool.update_hosts([other])
                assert [s.host.key for s in pool._slots] == [other.key]
                # 空列表 = 保持现状
                assert await pool.update_hosts([]) == pool.hosts
                await pool.close()

            run(go())

    def test_update_hosts_drops_removed_host(self) -> None:
        with FakeTdxServer() as a, FakeTdxServer() as b:

            async def go() -> None:
                host_a = HostEntry("127.0.0.1", a.port)
                host_b = HostEntry("127.0.0.1", b.port)
                pool = AsyncConnectionPool([host_a, host_b], slots_per_host=1, handshake=False)
                await pool.request(ECHO_CMD, b"x")
                assert pool._slots[0].conn is not None or pool._slots[1].conn is not None
                await pool.update_hosts([host_b])
                assert [s.host.key for s in pool._slots] == [host_b.key]
                assert any(s.retired for s in pool._retired_slots)
                await pool.close()

            run(go())

    def test_next_generation_host_carries_health_and_inflight_probe(self) -> None:
        """代际发布沿用池自身的运行期健康；在飞探测标记必须重开熔断。

        原 ``AsyncConnectionPool._inherit_runtime_health`` 是桩层里的第二份事实源，
        解散后与同步池共用 :func:`tstdx.transport.hosts.next_generation_host`。
        """
        old = HostEntry("h", 1, rtt_ms=30.0)
        old.live_rtt_ms = 12.0
        old.live_ok_at = 123.0
        old.failures = 2
        old.biz_failures = 1
        old.last_ok = 456.0
        old.last_error = "boom"
        old.consec_weighted = 0.5
        old.circuit_probe_inflight = True
        published = next_generation_host(old, HostEntry("h", 1, rtt_ms=99.0))
        assert published.live_rtt_ms == 12.0
        assert published.live_ok_at == 123.0
        assert published.failures == 2 and published.biz_failures == 1
        assert published.last_ok == 456.0 and published.last_error == "boom"
        assert published.consec_weighted == 0.5
        # 在飞探测标记 → 熔断态保留为 open，标记清空
        assert published.circuit == "open" and published.circuit_probe_inflight is False
        assert published.circuit_opened_at > 0.0
        # 探测证据仍会刷新，但不改写身份
        assert published.rtt_ms == 99.0 and published is not old


class TestAsyncPoolMultiFrame:
    def test_request_multi_merges_continuations(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                count, record_size, chunk = 6, 32, 64
                # record_size 必须显式传入：首帧体量 > 记录体量时无法由
                # 「首帧字节数 // 记录数」反推（同步/异步 request_multi 同语义）。
                frame = await pool.request_multi(
                    MULTI_CMD,
                    multi_body(count, record_size, chunk),
                    record_size=record_size,
                )
                assert len(frame.payload) == count * record_size
                assert frame.payload == multi_blob(count, record_size)
                await pool.close()

            run(go())

    def test_request_multi_without_count_returns_first_frame(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                frame = await pool.request_multi(ECHO_CMD, b"abc", expect_count=False)
                assert frame.payload[4:] == b"abc"
                await pool.close()

            run(go())

    def test_request_multi_first_frame_error_propagates(self) -> None:
        with FakeTdxServer() as server:
            server.close_on.add(MULTI_CMD)

            async def go() -> None:
                pool = _pool(server)
                with pytest.raises(TdxError):
                    await pool.request_multi(MULTI_CMD, multi_body(4, 32, 64))
                assert pool._slots[0].conn is None  # 弃连
                assert pool._slots[0].host.failures >= 1
                await pool.close()

            run(go())

    def test_request_multi_continuation_break_drops_conn(self) -> None:
        with FakeTdxServer() as server:
            # 首帧正常、随后立即断连 → 续帧中断，降级返回已合并块
            server.close_after.add(MULTI_CMD)

            async def go() -> None:
                pool = _pool(server)
                count, record_size, chunk = 8, 32, 32
                frame = await pool.request_multi(MULTI_CMD, multi_body(count, record_size, chunk))
                assert 0 < len(frame.payload) <= count * record_size
                await pool.close()

            run(go())

    def test_iter_frames_yields_all(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                count, record_size, chunk = 4, 32, 32
                seen: list[bytes] = []

                async def collect() -> AsyncIterator[None]:
                    # iter_frames 是「按帧数」的裸流原语（无记录数语义）：
                    # 必须显式给出上游将发出的帧数，否则末帧后一直读到超时。
                    async for frame in pool.iter_frames(
                        MULTI_CMD,
                        multi_body(count, record_size, chunk),
                        max_frames=count,
                    ):
                        seen.append(frame.payload)

                await collect()
                assert b"".join(seen)[2:] == multi_blob(count, record_size)
                assert len(seen) == count
                # 命中调用方给出的帧上限无法证明服务端流已结束——
                # 连接可能仍残留未读帧，必须弃连而非归还池。
                assert pool._slots[0].conn is None
                await pool.close()

            run(go())

    def test_iter_frames_early_close_drops_conn(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server)
                agen = pool.iter_frames(MULTI_CMD, multi_body(4, 32, 32))
                first = await agen.__anext__()
                assert first.payload
                await agen.aclose()  # GeneratorExit：残留帧使连接不可复用
                assert pool._slots[0].conn is None
                await pool.close()

            run(go())

    def test_iter_frames_error_mid_stream(self) -> None:
        with FakeTdxServer() as server:
            server.close_after.add(MULTI_CMD)

            async def go() -> None:
                pool = _pool(server)
                frames = []
                async for frame in pool.iter_frames(MULTI_CMD, multi_body(4, 32, 32)):
                    frames.append(frame)
                assert len(frames) >= 1
                assert pool._slots[0].host.failures >= 1
                await pool.close()

            run(go())


class TestAsyncPoolHeartbeat:
    def test_idle_slot_ping_updates_rtt(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, heartbeat_interval=1)
                slot = pool._slots[0]
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
                await conn.connect()
                conn.stats.last_used = time.time() - 60  # 强制判定为空闲
                slot.conn = conn
                task = asyncio.get_running_loop().create_task(pool._heartbeat_loop())
                await asyncio.sleep(1.4)
                assert slot.host.live_rtt_ms is not None
                assert slot.host.failures == 0
                pool._closed = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await pool.close()

            run(go())

    def test_ping_failure_drops_slot_connection(self) -> None:
        with FakeTdxServer() as server:
            # 心跳响应头声明 64 字节体但从不发出 → ping 读体超时
            server.hb_zip_size = 64

            async def go() -> None:
                pool = _pool(server, heartbeat_interval=1)
                slot = pool._slots[0]
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=0.2, handshake=False)
                await conn.connect()
                conn.stats.last_used = time.time() - 60
                slot.conn = conn
                task = asyncio.get_running_loop().create_task(pool._heartbeat_loop())
                await asyncio.sleep(1.4)
                assert slot.conn is None  # 探活失败 → 弃连
                assert slot.host.failures >= 1
                pool._closed = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await pool.close()

            run(go())

    def test_start_heartbeat_and_close_cancels_task(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, heartbeat_interval=60)
                async with pool as opened:
                    assert opened._hb is not None
                assert pool._hb is None
                assert pool._closed

            run(go())

    def test_heartbeat_respects_recent_activity(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, heartbeat_interval=1)
                slot = pool._slots[0]
                conn = AsyncTcpConnection("127.0.0.1", server.port, timeout=5.0, handshake=False)
                await conn.connect()
                conn.stats.last_used = time.time()  # 活跃：不探活
                slot.conn = conn
                task = asyncio.get_running_loop().create_task(pool._heartbeat_loop())
                await asyncio.sleep(1.3)
                assert slot.conn is conn and conn.connected
                assert conn.stats.requests == 0  # 未发出心跳帧
                pool._closed = True
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                await pool.close()

            run(go())


class TestAsyncPoolConstruction:
    def test_empty_hosts_rejected(self) -> None:
        with pytest.raises(ConfigError, match="不能为空"):
            AsyncConnectionPool([])

    def test_duplicate_endpoint_rejected(self) -> None:
        with pytest.raises(ConfigError, match="重复 canonical endpoint"):
            AsyncConnectionPool([HostEntry("127.0.0.1", 7709), HostEntry("127.0.0.1", 7709)])

    def test_handshake_default_follows_family(self) -> None:
        pool = AsyncConnectionPool([HostEntry("h", 1)], family=Family.STANDARD)
        assert pool.handshake is True
        # 非标准族默认不做握手；显式 handshake=True 优先
        ex = HostEntry("h", 1, family=Family.EXTENDED)
        assert AsyncConnectionPool([ex], family=Family.EXTENDED).handshake is False
        assert AsyncConnectionPool([HostEntry("h", 1)], handshake=True).handshake is True
        with pytest.raises(ConfigError, match="max_retries"):
            AsyncConnectionPool([HostEntry("h", 1)], max_retries=-3)

    def test_real_limiter_attached(self) -> None:
        with FakeTdxServer() as server:

            async def go() -> None:
                pool = _pool(server, rate_limiter=SessionRateLimiter(strict=False))
                frame = await pool.request(ECHO_CMD, b"q")
                assert frame.payload[4:] == b"q"
                await pool.close()

            run(go())
