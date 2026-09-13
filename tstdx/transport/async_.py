# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""异步（asyncio）传输层（§13）。

与 :mod:`tstdx.transport.base` 一一对应，API 形状保持一致，
便于同步/异步双轨共用上层的请求构造与解析逻辑。

差异点
------
* 超时用 :func:`asyncio.wait_for`，不用 ``settimeout``。
* 锁用 :class:`asyncio.Lock`。
* 探活用 :class:`asyncio.Task` 而非线程。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import ssl
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..codec.framing import (
    DEFAULT_7709_SPEC,
    FrameSpec,
    ResponseFrame,
    build_request,
    decode_response_body,
    parse_response_header,
)
from ..errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
    AllHostsUnreachable,
    ConnectionClosed,
    ConnectionFailed,
    FramingError,
    ProtocolError,
    ReadTimeout,
    TdxError,
    WriteTimeout,
)
from ..observability import metrics
from ..protocol.commands import Family
from .base import DEFAULT_HEARTBEAT_CMD, ConnectionStats
from .hosts import HostEntry
from .ratelimit import SessionRateLimiter

__all__ = [
    "AsyncTcpConnection",
    "AsyncSlot",
    "AsyncConnectionPool",
]

#: 传输域统一 logger（只获取，不配置 handler——配置交给宿主应用）。
_LOG = logging.getLogger("tstdx.transport")

_RECV_CHUNK = 65536


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
class AsyncTcpConnection:
    """基于 :mod:`asyncio` 的单条连接。"""

    def __init__(
        self,
        host: str,
        port: int = 7709,
        *,
        timeout: float = 3.0,
        connect_timeout: float | None = None,
        spec: FrameSpec = DEFAULT_7709_SPEC,
        use_tls: bool = False,
        tls_context: ssl.SSLContext | None = None,
        slot_id: int = 0,
        family: str = Family.STANDARD,
        handshake: bool = True,
        handshake_strict: bool = False,
        handshake_blob: bytes | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.connect_timeout = float(connect_timeout or timeout)
        self.spec = spec
        self.use_tls = use_tls
        self.tls_context = tls_context
        self.slot_id = slot_id
        self.family = family
        self.handshake = handshake
        self.handshake_strict = handshake_strict
        self.handshake_blob = handshake_blob

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._handshaked = False
        self._lock = asyncio.Lock()
        self.stats = ConnectionStats()

    # -- 属性 --------------------------------------------------------------- #
    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AsyncTcpConnection {self.host}:{self.port}#{self.slot_id} {'up' if self.connected else 'down'}>"

    # -- 生命周期 ----------------------------------------------------------- #
    async def connect(self) -> AsyncTcpConnection:
        if self.connected:
            return self
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self.host,
                    self.port,
                    ssl=self.tls_context if self.use_tls else None,
                ),
                timeout=self.connect_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ConnectionFailed(
                f"连接 {self.host}:{self.port} 超时({self.connect_timeout}s)",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc
        except OSError as exc:
            raise ConnectionFailed(
                f"连接 {self.host}:{self.port} 失败: {exc}",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc
        try:
            sock = writer.transport.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(6, 1, 1)  # IPPROTO_TCP=6, TCP_NODELAY=1
        except Exception:
            pass
        self._reader, self._writer = reader, writer
        self._seq = 0
        self._handshaked = False
        self.stats.reconnects += 1
        if self.handshake:
            await self._send_setup()
        return self

    # -- 握手 --------------------------------------------------------------- #
    async def _send_setup(self) -> None:
        """发送握手帧并丢弃响应（语义与同步版一致）。"""
        from ..protocol.handshake import setup_frames

        frames = setup_frames(self.family, blob=self.handshake_blob)
        if not frames:
            self._handshaked = True
            return
        for raw in frames:
            try:
                self._require_reader()
                assert self._writer is not None
                self._writer.write(raw)
                await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)
                self.stats.bytes_sent += len(raw)
                await self._read_frame_locked()
            except TdxError as exc:
                self._handshaked = False
                self.stats.last_error = f"handshake: {type(exc).__name__}: {exc}"
                if self.handshake_strict:
                    await self.close()
                    raise
                return
        self._handshaked = True
        self._seq = 0

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def __aenter__(self) -> AsyncTcpConnection:
        return await self.connect()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- 帧 IO -------------------------------------------------------------- #
    def _require_reader(self) -> asyncio.StreamReader:
        if self._reader is None or self._writer is None:
            raise ConnectionClosed(
                f"连接已关闭: {self.host}:{self.port}",
                context={"host": self.host, "port": self.port},
            )
        return self._reader

    async def _recv_exact(self, size: int) -> bytes:
        reader = self._require_reader()
        try:
            data = await asyncio.wait_for(reader.readexactly(size), timeout=self.timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            # T1 跨版本归类：3.10- asyncio.TimeoutError 非 OSError，3.11+
            # 是内建 TimeoutError（OSError 子类）。显式双类型 + 先于
            # ``except OSError``，保证任何版本都归类为 ReadTimeout。
            raise ReadTimeout(
                f"读取超时({self.timeout}s): 需要 {size} 字节",
                context={"host": self.host, "port": self.port, "need": size},
                cause=exc,
            ) from exc
        except asyncio.IncompleteReadError as exc:
            await self.close()
            raise ConnectionClosed(
                "对端关闭连接", context={"host": self.host, "port": self.port}, cause=exc
            ) from exc
        except OSError as exc:
            await self.close()
            raise ConnectionClosed(
                f"读取失败: {exc}", context={"host": self.host, "port": self.port}, cause=exc
            ) from exc
        self.stats.bytes_recv += len(data)
        return data

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    async def read_frame(self) -> ResponseFrame:
        """读取并解码一个完整响应帧（公开原子入口）。

        T#2：asyncio.Lock 不可重入——持锁路径（_request_locked / request_multi
        租约 / iter_frames 租约 / _send_setup）必须调用 :meth:`_read_frame_locked`；
        本入口供无外层锁的独立调用（原子性由连接锁保证）。
        """
        async with self._lock:
            return await self._read_frame_locked()

    async def _read_frame_locked(self) -> ResponseFrame:
        """read_frame 的无锁核心（调用方必须已持有 :attr:`_lock`）。"""
        header = await self._recv_exact(self.spec.resp_header_size)
        frame = parse_response_header(header, self.spec)
        if frame.magic != self.spec.magic:
            raise FramingError(
                f"magic 不匹配: 收到 {hex(frame.magic)}",
                context={"host": self.host, "port": self.port},
            )
        if frame.zip_size > self.spec.max_frame_bytes:
            raise FramingError(
                f"响应帧过大: {frame.zip_size}", context={"zip_size": frame.zip_size}
            )
        body = await self._recv_exact(frame.zip_size)
        return decode_response_body(frame, body, strict=True)

    # -- 请求 --------------------------------------------------------------- #
    async def request(
        self,
        method: int,
        body: bytes = b"",
        *,
        check_seq: bool = True,
        compress: bool = False,
        timeout: float | None = None,
    ) -> ResponseFrame:
        # C5：与同步版对齐的连接级租约——建连、seq 分配、发帧、收帧
        # 全程持锁；ping/续帧/iter_frames 与在飞请求在锁上天然串行。
        # 注意：asyncio.Lock 不可重入——池的 request_multi 持锁续帧时
        # 必须调用 _request_locked()，不得再走本入口。
        async with self._lock:
            return await self._request_locked(
                method, body, check_seq=check_seq, compress=compress, timeout=timeout
            )

    async def _request_locked(
        self,
        method: int,
        body: bytes = b"",
        *,
        check_seq: bool = True,
        compress: bool = False,
        timeout: float | None = None,
    ) -> ResponseFrame:
        """请求主流程（调用方必须已持有 ``self._lock``）。"""
        if not self.connected:
            await self.connect()
        seq = self.next_seq()
        frame_bytes, seq = build_request(method, body, seq=seq, spec=self.spec, compress=compress)
        self.stats.requests += 1
        self.stats.last_used = time.time()
        saved = self.timeout
        if timeout is not None:
            self.timeout = float(timeout)
        try:
            self._require_reader()
            assert self._writer is not None
            try:
                self._writer.write(frame_bytes)
                await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                # T1 跨版本归类：3.10- asyncio.TimeoutError 是
                # concurrent.futures.TimeoutError（非 OSError）；3.11+
                # 两者同为内建 TimeoutError（OSError 子类）。显式列出
                # 双类型保证任何版本都归类为 WriteTimeout，其 advice
                # 才能被消费（而非落入 OSError 分支变成 ConnectionClosed）。
                # 注意本分支必须位于 ``except OSError`` 之前（3.11+ 上
                # TimeoutError 是 OSError 的子类）。
                raise WriteTimeout(
                    f"发送超时({self.timeout}s)",
                    context={"host": self.host, "port": self.port},
                    cause=exc,
                ) from exc
            except OSError as exc:
                await self.close()
                raise ConnectionClosed(
                    f"发送失败: {exc}",
                    context={"host": self.host, "port": self.port},
                    cause=exc,
                ) from exc
            self.stats.bytes_sent += len(frame_bytes)
            frame = await self._read_frame_locked()
        except TdxError as exc:
            self.stats.failures += 1
            self.stats.last_error = f"{type(exc).__name__}: {exc}"
            await self.close()
            raise
        except Exception:  # pragma: no cover
            self.stats.failures += 1
            await self.close()
            raise
        finally:
            self.timeout = saved

        if check_seq and frame.seq != seq:
            await self.close()
            raise ProtocolError(
                f"响应 seq 不匹配: 期望 {seq}，收到 {frame.seq}",
                context={"expect": seq, "got": frame.seq, "method": hex(method)},
            )
        return frame

    async def ping(self, cmd: int = DEFAULT_HEARTBEAT_CMD, body: bytes = b"") -> float:
        """探活（C5）：全程持连接锁，与 request/续帧读写互斥。

        旧实现在 ``conn._lock`` 之外直接 write/drain/read——探活帧会插入
        在飞请求的读序造成交叉，且 ``next_seq()`` 锁外自增可能撞号。
        """
        async with self._lock:
            if not self.connected:
                await self.connect()
            frame_bytes, _ = build_request(cmd, body, seq=self.next_seq(), spec=self.spec)
            started = time.perf_counter()
            try:
                assert self._writer is not None
                self._writer.write(frame_bytes)
                await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)
                header = await self._recv_exact(self.spec.resp_header_size)
                frame = parse_response_header(header, self.spec)
                if frame.zip_size:
                    # P1：对齐 read_frame 的 max_frame_bytes 校验——错位流下的
                    # 垃圾 zip_size 不得触发无上限长读。
                    if frame.zip_size > self.spec.max_frame_bytes:
                        raise FramingError(
                            f"探活响应帧过大: {frame.zip_size} > {self.spec.max_frame_bytes}",
                            context={"zip_size": frame.zip_size},
                        )
                    await self._recv_exact(frame.zip_size)
            except TdxError:
                await self.close()
                raise
            except Exception as exc:
                await self.close()
                raise ConnectionClosed(
                    f"探活失败: {exc}", context={"host": self.host, "port": self.port}, cause=exc
                ) from exc
            return (time.perf_counter() - started) * 1000.0

    def health(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "slot": self.slot_id,
            "family": self.family,
            "connected": self.connected,
            "handshaked": self._handshaked,
            **self.stats.to_dict(),
        }


# --------------------------------------------------------------------------- #
# 异步槽位与池
# --------------------------------------------------------------------------- #
@dataclass
class AsyncSlot:
    host: HostEntry
    index: int
    conn: AsyncTcpConnection | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    uses: int = 0
    generation: int = 0
    leases: int = 0
    retired: bool = False

    @property
    def key(self) -> str:
        return f"{self.host.key}#{self.index}"


class AsyncConnectionPool:
    """异步连接池：与 :class:`ConnectionPool` 同构。"""

    def __init__(
        self,
        hosts: Sequence[HostEntry],
        *,
        family: str = Family.STANDARD,
        slots_per_host: int = 4,
        timeout: float = 3.0,
        connect_timeout: float | None = None,
        rate_limiter: SessionRateLimiter | None = None,
        heartbeat_interval: int | None = 30,
        heartbeat_cmd: int = DEFAULT_HEARTBEAT_CMD,
        max_retries: int = 3,
        spec: FrameSpec | None = None,
        use_tls: bool = False,
        handshake: bool | None = None,
        handshake_strict: bool = False,
    ) -> None:
        if not hosts:
            raise ValueError("hosts 不能为空")
        self.family = family
        self.handshake = (
            family in (Family.STANDARD, Family.MAC) if handshake is None else bool(handshake)
        )
        self.handshake_strict = bool(handshake_strict)
        self.hosts = list(hosts)
        self.slots_per_host = max(1, slots_per_host)
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.rate_limiter = rate_limiter
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_cmd = heartbeat_cmd
        self.max_retries = max(0, max_retries)
        self.spec = spec
        self.use_tls = use_tls

        self._slots: list[AsyncSlot] = [
            AsyncSlot(host=h, index=i, generation=0)
            for h in self.hosts
            for i in range(self.slots_per_host)
        ]
        self._rr = 0
        self._lock = asyncio.Lock()
        self._generation = 0
        self._retired_slots: list[AsyncSlot] = []
        self._closed = False
        self._hb: asyncio.Task | None = None

    # -- 槽位 --------------------------------------------------------------- #
    def _ordered(self) -> list[AsyncSlot]:
        n = len(self._slots)
        if n == 0:
            return []
        start = self._rr % n
        self._rr = (self._rr + 1) % n
        ordered = self._slots[start:] + self._slots[:start]
        return sorted(ordered, key=lambda s: s.host.score)

    async def _get_conn_locked(self, slot: AsyncSlot) -> AsyncTcpConnection:
        if slot.retired:
            raise ConnectionClosed("槽位已从连接池代际中移除")
        if slot.conn is None:
            slot.conn = AsyncTcpConnection(
                slot.host.host,
                slot.host.port,
                timeout=self.timeout,
                connect_timeout=self.connect_timeout,
                use_tls=self.use_tls,
                slot_id=slot.index,
                family=self.family,
                handshake=self.handshake,
                handshake_strict=self.handshake_strict,
            )
            if self.spec is not None:
                slot.conn.spec = self.spec
        if not slot.conn.connected:
            await slot.conn.connect()
        slot.uses += 1
        return slot.conn

    async def _get_conn(self, slot: AsyncSlot) -> AsyncTcpConnection:
        async with slot.lock:
            return await self._get_conn_locked(slot)

    async def _acquire_lease(self, slot: AsyncSlot) -> tuple[AsyncTcpConnection, int]:
        async with slot.lock:
            conn = await self._get_conn_locked(slot)
            slot.leases += 1
            return conn, slot.generation

    async def _release_lease(self, slot: AsyncSlot, conn: AsyncTcpConnection) -> None:
        async with slot.lock:
            slot.leases = max(0, slot.leases - 1)
            should_drop = slot.retired and slot.leases == 0
        if should_drop:
            await self._drop(slot, expected=conn)

    async def _drop(self, slot: AsyncSlot, *, expected: AsyncTcpConnection | None = None) -> None:
        async with slot.lock:
            conn = slot.conn
            if conn is None or (expected is not None and conn is not expected):
                return
            async with conn._lock:
                with contextlib.suppress(Exception):
                    await conn.close()
            if slot.conn is conn:
                slot.conn = None

    async def _slot_is_current(self, slot: AsyncSlot, generation: int) -> bool:
        async with self._lock:
            return (
                not slot.retired
                and slot.generation == generation
                and slot in self._slots
                and not self._closed
            )

    # -- 请求 --------------------------------------------------------------- #
    def _ensure_open(self) -> None:
        if self._closed:
            raise ConnectionClosed("连接池已关闭")

    async def _acquire_rate(self) -> None:
        """异步友好的限流取令牌。

        深审 M3：旧实现三处缺口——request 的 sleep 后**忽略**第二次
        try_acquire 结果（无论成败都放行，限流形同虚设）；request_multi /
        iter_frames 则完全绕过限流器。统一收口于此：strict 模式超速直接抛
        RateLimitedLocal（对齐同步池语义）；否则异步等待令牌，不阻塞事件循环。
        """
        limiter = self.rate_limiter
        if limiter is None:
            return
        if limiter.strict:
            limiter.acquire()
            return
        while not limiter.try_acquire():
            await asyncio.sleep(0.05)

    async def request(
        self, method: int, body: bytes = b"", *, timeout: float | None = None
    ) -> ResponseFrame:
        self._ensure_open()
        await self._acquire_rate()

        last_exc: BaseException | None = None
        tried: list[str] = []
        started = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            # T5：close() 与在飞请求竞态——每轮复查，池已关则终止重建。
            self._ensure_open()
            slots = self._ordered()
            if not slots:
                break
            # P1：tried_hosts 生效于选序（与同步池一致），全部试过则退回 slots[0]。
            slot = next((s for s in slots if s.host.key not in tried), slots[0])
            tried.append(slot.host.key)
            conn: AsyncTcpConnection | None = None
            generation: int | None = None
            try:
                conn, generation = await self._acquire_lease(slot)
                try:
                    frame = await conn.request(method, body, timeout=timeout)
                except BaseException:
                    await self._release_lease(slot, conn)
                    raise
                await self._release_lease(slot, conn)
            except TdxError as exc:
                last_exc = exc
                if generation is None or await self._slot_is_current(slot, generation):
                    slot.host.failures += 1
                    slot.host.last_error = f"{type(exc).__name__}: {exc}"
                _LOG.warning("异步槽位 %s 请求失败: %s", slot.key, exc)
                metrics.record_error(type(exc).__name__)
                await self._drop(slot, expected=conn)
                advice = exc.advice
                if attempt + 1 > self.max_retries or not advice.retryable:
                    break
                if advice.switch_host:
                    _LOG.info("异步换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                    self._rotate_away(slot.host.key)
                if advice.backoff:
                    delay = advice.backoff * (2**attempt) * (0.75 + 0.5 * random.random())
                    _LOG.debug("异步退避 %.3fs 后重试（%s）", delay, type(exc).__name__)
                    await asyncio.sleep(delay)
                continue
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                if generation is None or await self._slot_is_current(slot, generation):
                    slot.host.failures += 1
                await self._drop(slot, expected=conn)
                if attempt + 1 > self.max_retries:
                    break
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            if generation is None or await self._slot_is_current(slot, generation):
                slot.host.failures = 0
                slot.host.last_ok = time.time()
                slot.host.live_rtt_ms = (time.perf_counter() - started) * 1000.0
                slot.host.live_ok_at = slot.host.last_ok
            metrics.record_request(
                command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
            )
            return frame

        metrics.record_request(command=f"0x{method:04x}", ok=False)
        raise AllHostsUnreachable(
            f"所有主站均不可达（已尝试 {tried}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
            context={"hosts": tried, "method": hex(method)},
            cause=last_exc,
        )

    def _rotate_away(self, host_key: str) -> None:
        for i, slot in enumerate(self._slots):
            if slot.host.key != host_key:
                self._rr = i
                return

    # -- bestip 热更新（镜像同步版） ----------------------------------------- #
    @staticmethod
    def _inherit_runtime_health(old: HostEntry, new: HostEntry) -> None:
        new.live_rtt_ms = old.live_rtt_ms
        new.live_ok_at = old.live_ok_at
        new.failures = max(old.failures, new.failures)
        new.biz_failures = max(old.biz_failures, new.biz_failures)
        if old.last_ok is not None:
            new.last_ok = old.last_ok
        if old.last_error:
            new.last_error = old.last_error
        new.consec_weighted = old.consec_weighted
        if old.circuit_probe_inflight:
            new.circuit = "open"
            new.circuit_opened_at = time.time()
        else:
            new.circuit = old.circuit
            new.circuit_opened_at = old.circuit_opened_at
        new.circuit_probe_inflight = False

    async def update_hosts(self, hosts: Sequence[HostEntry]) -> list[HostEntry]:
        """按新序重建主站池，复用现有连接（镜像 :meth:`ConnectionPool.update_hosts`）。

        ``bestip`` 运行时测速后调用：保留仍在新列表里的槽位连接并刷新其
        ``HostEntry``（带入新 RTT / 失败计数），丢弃被移除主站的连接。
        """
        if not hosts:
            return list(self.hosts)
        new_hosts = [h for h in hosts if h is not None]
        to_close: list[AsyncSlot] = []
        async with self._lock:
            self._generation += 1
            generation = self._generation
            old_by_key = {s.key: s for s in self._slots}
            new_slots: list[AsyncSlot] = []
            for host in new_hosts:
                for i in range(self.slots_per_host):
                    old = old_by_key.get(f"{host.key}#{i}")
                    if old is None:
                        new_slots.append(AsyncSlot(host=host, index=i, generation=generation))
                        continue
                    async with old.lock:
                        self._inherit_runtime_health(old.host, host)
                        if old.leases == 0 and not old.retired:
                            old.host = host
                            old.generation = generation
                            new_slots.append(old)
                        else:
                            old.retired = True
                            self._retired_slots.append(old)
                            new_slots.append(AsyncSlot(host=host, index=i, generation=generation))
            new_keys = {s.key for s in new_slots}
            for old in self._slots:
                if old.key in new_keys:
                    continue
                async with old.lock:
                    old.retired = True
                    if old not in self._retired_slots:
                        self._retired_slots.append(old)
                    if old.leases == 0:
                        to_close.append(old)
            self.hosts = new_hosts
            self._slots = new_slots
            self._rr = 0
        for old in to_close:
            await self._drop(old)
        return new_hosts

    # -- 多帧 --------------------------------------------------------------- #
    async def request_multi(
        self,
        method: int,
        body: bytes = b"",
        *,
        record_size: int | None = None,
        expect_count: bool = True,
        max_frames: int = 512,
        timeout: float | None = None,
    ) -> ResponseFrame:
        self._ensure_open()
        await self._acquire_rate()
        slot = self._ordered()[0]
        conn, generation = await self._acquire_lease(slot)
        first_exc: TdxError | None = None
        cont_exc: TdxError | None = None
        result: ResponseFrame | None = None
        started = time.perf_counter()
        # C6：首帧 + 全部续帧在同一连接租约（conn._lock）内读完，期间
        # 其它请求/心跳无法插入同一 socket 的读写序。弃连（_drop 需取
        # slot.lock）延迟到锁外执行——锁序恒为 slot.lock → conn._lock。
        async with conn._lock:
            try:
                # asyncio.Lock 不可重入：持锁续帧期间必须走 _request_locked
                # （conn.request 会再次取锁 → 自死锁）。
                first = await conn._request_locked(method, body, timeout=timeout)
            except TdxError as exc:
                first_exc = exc
            else:
                payload = first.payload
                if not expect_count or len(payload) < 2:
                    metrics.record_request(
                        command=f"0x{method:04x}",
                        ok=True,
                        duration=time.perf_counter() - started,
                    )
                    await self._release_lease(slot, conn)
                    return first
                count = int.from_bytes(payload[:2], "little")
                chunks = [payload[2:]]
                got = len(chunks[0])
                if count > 0 and record_size is None and got > 0:
                    record_size = max(1, got // count)
                need = count * (record_size or 1) if record_size else None
                read = 1
                while need is not None and got < need and read < max_frames:
                    try:
                        nxt = await conn._read_frame_locked()
                    except TdxError as exc:
                        cont_exc = exc
                        _LOG.warning(
                            "异步 request_multi 续帧中断（%s: %s），降级返回已合并的 %d 块",
                            type(exc).__name__,
                            exc,
                            len(chunks),
                        )
                        break
                    read += 1
                    if not nxt.payload:
                        break
                    chunks.append(nxt.payload)
                    got += len(nxt.payload)
                result = ResponseFrame(
                    magic=first.magic,
                    zip_flag=first.zip_flag,
                    seq=first.seq,
                    method=first.method,
                    zip_size=sum(len(c) for c in chunks),
                    unzip_size=sum(len(c) for c in chunks),
                    body=b"",
                    payload=b"".join(chunks),
                    header_raw=first.header_raw,
                )
                metrics.record_request(
                    command=f"0x{method:04x}",
                    ok=cont_exc is None,
                    duration=time.perf_counter() - started,
                )
        await self._release_lease(slot, conn)
        if first_exc is not None:
            if await self._slot_is_current(slot, generation):
                slot.host.failures += 1
                slot.host.last_error = f"{type(first_exc).__name__}: {first_exc}"
            await self._drop(slot, expected=conn)
            raise first_exc
        if cont_exc is not None:
            # C6：对齐同步池 _mark_failure → _drop 语义——续帧中断一律弃连。
            # 旧实现 ``except TdxError: break`` 不断连，超时后 reader 缓冲里
            # 残留的半帧会污染该槽位的下一个请求（经典半包）。
            if await self._slot_is_current(slot, generation):
                slot.host.failures += 1
                slot.host.last_error = f"{type(cont_exc).__name__}: {cont_exc}"
            await self._drop(slot, expected=conn)
        assert result is not None
        return result

    async def iter_frames(
        self, method: int, body: bytes = b"", *, max_frames: int = 512
    ) -> AsyncIterator[ResponseFrame]:
        self._ensure_open()
        await self._acquire_rate()
        slot = self._ordered()[0]
        conn, generation = await self._acquire_lease(slot)
        # T#2 整体租约：seq 分配、写帧、逐帧读取全程持连接锁——心跳与并发
        # 请求无法插入读写序；逐帧 touch last_used，心跳空闲判定对活跃流
        # 让路（不再排队等锁）。消费方弃读（GeneratorExit / aclose）时
        # socket 内残留未读帧，连接不可复用——弃连。
        failed: TdxError | None = None
        try:
            async with conn._lock:
                frame_bytes, _ = build_request(method, body, seq=conn.next_seq(), spec=conn.spec)
                writer = conn._writer
                if writer is None:
                    raise ConnectionClosed(
                        f"连接已关闭: {conn.host}:{conn.port}",
                        context={"host": conn.host, "port": conn.port},
                    )
                try:
                    writer.write(frame_bytes)
                    await asyncio.wait_for(writer.drain(), timeout=conn.timeout)
                    conn.stats.bytes_sent += len(frame_bytes)
                except TdxError as exc:
                    failed = exc
                except OSError as exc:
                    failed = ConnectionClosed(
                        f"发送失败: {exc}",
                        context={"host": conn.host, "port": conn.port},
                        cause=exc,
                    )
                if failed is None:
                    for _ in range(max_frames):
                        try:
                            frame = await conn._read_frame_locked()
                        except TdxError as exc:
                            failed = exc
                            break
                        conn.stats.last_used = time.time()
                        yield frame
        except GeneratorExit:
            await self._drop(slot, expected=conn)
            raise
        finally:
            await self._release_lease(slot, conn)
        if failed is not None:
            _LOG.warning("异步 iter_frames 中断（%s: %s），弃连", type(failed).__name__, failed)
            if await self._slot_is_current(slot, generation):
                slot.host.failures += 1
                slot.host.last_error = f"{type(failed).__name__}: {failed}"
            await self._drop(slot, expected=conn)
            return
        # C6：正常结束不弃连（连接归还槽位），但需复查池状态——
        # 迭代期间池被 close 的话，不留活连接。
        if self._closed:
            await self._drop(slot, expected=conn)

    # -- 生命周期 ----------------------------------------------------------- #
    async def _heartbeat_loop(self) -> None:
        interval = max(1, int(self.heartbeat_interval or 0))
        while not self._closed:
            await asyncio.sleep(interval)
            for slot in list(self._slots):
                if self._closed:
                    return
                # T5：每轮从 slot 重取 conn 引用，绝不跨 await 持旧引用做决策——
                # ping 挂起期间该槽位可能已被请求路径弃连并重建。
                try:
                    conn, generation = await self._acquire_lease(slot)
                except TdxError:
                    continue
                idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                if idle < interval:
                    await self._release_lease(slot, conn)
                    continue
                try:
                    rtt = await conn.ping(self.heartbeat_cmd)
                except Exception as exc:
                    _LOG.warning("异步心跳失败 %s: %s", slot.key, exc)
                    if await self._slot_is_current(slot, generation):
                        slot.host.failures += 1
                    if slot.conn is conn:
                        # 只弃自己探测的那条连接；探测期间 slot.conn 已换成
                        # 新建连接时不得误杀（否则新连接被孤儿化/反复重建）。
                        await self._drop(slot, expected=conn)
                else:
                    if await self._slot_is_current(slot, generation) and slot.conn is conn:
                        slot.host.live_rtt_ms = rtt
                        slot.host.live_ok_at = time.time()
                        slot.host.failures = 0
                        slot.host.last_ok = time.time()
                await self._release_lease(slot, conn)

    def start_heartbeat(self) -> None:
        if self.heartbeat_interval and self._hb is None:
            # T5：get_event_loop() 在 3.12+ 的运行中协程里已弃用/报错，
            # 统一用 get_running_loop()。
            self._hb = asyncio.get_running_loop().create_task(self._heartbeat_loop())

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            slots = list(self._slots) + list(self._retired_slots)
            for slot in slots:
                async with slot.lock:
                    slot.retired = True
        if self._hb is not None:
            self._hb.cancel()
            self._hb = None
        for slot in slots:
            await self._drop(slot)

    async def __aenter__(self) -> AsyncConnectionPool:
        self.start_heartbeat()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def health(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "hosts": [
                {
                    "host": h.host,
                    "port": h.port,
                    "rtt_ms": h.rtt_ms,
                    "live_rtt_ms": h.live_rtt_ms,
                    "failures": h.failures,
                    "circuit": h.circuit,
                }
                for h in self.hosts
            ],
            "slots": [
                {
                    "slot": s.key,
                    "connected": bool(s.conn and s.conn.connected),
                    "uses": s.uses,
                    "generation": s.generation,
                    "leases": s.leases,
                }
                for s in self._slots
            ],
        }
