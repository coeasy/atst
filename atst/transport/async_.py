# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""异步（asyncio）传输层（§13）。

与 :mod:`atst.transport.base` 一一对应，API 形状保持一致，
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
    RateLimitedLocal,
    ReadTimeout,
    TdxError,
    WriteTimeout,
)
from ..observability import metrics
from ..protocol.commands import Family
from ._validation import (
    canonical_family_hosts,
    require_bool,
    require_timeout,
    validate_common_connection_args,
    validate_common_pool_options,
)
from .base import DEFAULT_HEARTBEAT_CMD, ConnectionStats
from .hosts import (
    HostEntry,
    new_endpoint_entry,
    next_generation_host,
    validate_host_updates,
)
from .pool import (
    BIZ_FAILURE_WEIGHT,
    CIRCUIT_COOLDOWN_SECONDS,
    CIRCUIT_DEGRADED_AT,
    CIRCUIT_OPEN_AT,
    _require_bool_option,
    _require_positive_frame_limit,
    _require_request_timeout,
    retry_backoff_delay,
)
from .ratelimit import DEFAULT_ACQUIRE_TIMEOUT, SessionRateLimiter

__all__ = [
    "AsyncTcpConnection",
    "AsyncSlot",
    "AsyncConnectionPool",
]

#: 传输域统一 logger（只获取，不配置 handler——配置交给宿主应用）。
_LOG = logging.getLogger("atst.transport")

_RECV_CHUNK = 65536

#: :func:`_await_cleanup_before_cancellation` 等待清理任务的硬上界（秒）。
#: 没有它时，反复投递取消会把那段 ``shield`` 循环变成满速空转。
_CLEANUP_SHIELD_TIMEOUT: float = 30.0

#: :meth:`AsyncTcpConnection.close` 等待传输收尾的墙钟上限（秒）。
#:
#: ``writer.wait_closed()`` 等的是 asyncio 协议的 ``connection_lost`` 回调，而 ``close()``
#: 之后这个回调**不一定到来**：selector 传输要先排空发送缓冲才断开，对端不再读取时它会
#: 一直挂着（CPython 3.12 的 ``streams.StreamWriter.wait_closed`` 就在 await 那个 future）。
#: TLS 那条路由 :data:`asyncio.constants.SSL_SHUTDOWN_TIMEOUT` 兜底，现值 30.0 秒——
#: 而池的关停是**逐个槽位**排空（:meth:`AsyncConnectionPool._cleanup_committed_close`），
#: 于是"关不掉"的代价按 N 个槽位乘上去。
#:
#: 为什么是 1 秒而不是接到 ``self.timeout``：调用这行时套接字已经 ``close()`` 过了，
#: 等待只为收掉一次回调，健康循环里它在一个 tick 内就返回；把它绑到请求超时（默认 3 秒、
#: 可配到更大）会让关停无端比"探活一次"还慢。真挂住的连接也不会拖住调用方——最坏
#: ``槽位数 × 1 秒``。放弃等待不会漏掉释放：套接字由 ``connection_lost`` 自己关，有没有人
#: 在 await 那个 future 不参与它的时机。
CLOSE_WAIT_SECONDS = 1.0


# --------------------------------------------------------------------------- #
# 熔断异常构造（入参校验原语复用 pool 的同一套 fail-closed 口径）
# --------------------------------------------------------------------------- #
def _circuit_unreachable(method: int, *, attempts: int = 0) -> AllHostsUnreachable:
    cause = ConnectionFailed("所有候选主站均处于熔断门禁")
    return AllHostsUnreachable(
        f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
        context={"hosts": [], "method": hex(method), "attempts": attempts},
        cause=cause,
    )


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
        # --- fail-closed contract validation (merged from hardening) --------- #
        (
            self.host,
            self.port,
            self.family,
            self.timeout,
            self.connect_timeout,
            self.use_tls,
            self.slot_id,
            self.handshake,
            self.handshake_strict,
            self.handshake_blob,
        ) = validate_common_connection_args(
            host=host,
            port=port,
            family=family,
            timeout=timeout,
            connect_timeout=connect_timeout,
            use_tls=use_tls,
            tls_context=tls_context,
            slot_id=slot_id,
            handshake=handshake,
            handshake_strict=handshake_strict,
            handshake_blob=handshake_blob,
        )
        self.tls_context = tls_context
        self.spec = spec
        self.connect_timeout = (
            self.connect_timeout if self.connect_timeout is not None else self.timeout
        )

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
        """建连（公开原子入口）。

        T5（TOCTOU，第 26 轮 F-94）：与同步版 :meth:`TcpConnection.connect` 同一判据——
        检查与建连必须在同一临界区内完成。旧实现在锁外读 ``self.connected``，两个协程同时看到
        "未连接"会各自 ``open_connection``，先建的那条 writer 被后建的覆盖后永久泄漏
        （socket 一直开着，没人关也没人读）。
        ``asyncio.Lock`` 不可重入，所以持锁路径（``_request_locked`` / ``ping``）必须
        走 :meth:`_connect_locked`，不得再进本入口。
        """
        async with self._lock:
            return await self._connect_locked()

    async def _connect_locked(self) -> AsyncTcpConnection:
        """``connect()`` 的无锁核心（调用方必须已持有 :attr:`_lock`）。"""
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
            _LOG.warning("连接 %s:%s 超时(%.2fs)", self.host, self.port, self.connect_timeout)
            raise ConnectionFailed(
                f"连接 {self.host}:{self.port} 超时({self.connect_timeout}s)",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc
        except OSError as exc:
            _LOG.warning("连接 %s:%s 失败: %s", self.host, self.port, exc)
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
                await asyncio.wait_for(writer.wait_closed(), timeout=CLOSE_WAIT_SECONDS)
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

    async def read_frame(self, timeout: float | None = None) -> ResponseFrame:
        """读取并解码一个完整响应帧（公开原子入口）。

        T#2：asyncio.Lock 不可重入——持锁路径（_request_locked / request_multi
        租约 / iter_frames 租约 / _send_setup）必须调用 :meth:`_read_frame_locked`；
        本入口供无外层锁的独立调用（原子性由连接锁保证）。

        `timeout` 与 :meth:`request` 的同名参数同义：只对本次读帧生效（改的是
        持锁期间的 :attr:`timeout`，退出时复原），缺省沿用连接自身的超时。
        """
        async with self._lock:
            saved = self.timeout
            if timeout is not None:
                self.timeout = float(timeout)
            try:
                return await self._read_frame_locked()
            finally:
                self.timeout = saved

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
        # --- fail-closed contract validation (merged from hardening) --------- #
        require_bool("check_seq", check_seq)
        require_bool("compress", compress)
        timeout = require_timeout("request.timeout", timeout, allow_none=True)
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
        # 已持锁：必须走无锁核心，否则 asyncio.Lock 不可重入 → 自死锁。
        await self._connect_locked()
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
            # 已持锁：走无锁核心，理由同 _request_locked。
            await self._connect_locked()
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


# --------------------------------------------------------------------------- #
# 关停事务辅助
# --------------------------------------------------------------------------- #
def _unique_slots(pool: AsyncConnectionPool) -> list[AsyncSlot]:
    slots: list[AsyncSlot] = []
    seen: set[int] = set()
    for slot in [*pool._slots, *pool._retired_slots]:
        identity = id(slot)
        if identity in seen:
            continue
        seen.add(identity)
        slots.append(slot)
    return slots


async def _await_cleanup_before_cancellation(cleanup_task: asyncio.Task) -> None:
    """把清理与调用方取消解耦：先排空，再决定是否传播取消。

    循环自带 deadline（与 :class:`AsyncQuoteStream.stop` 同一条口径）：监督方若反复
    投递取消，``await`` 每次立刻抛 ``CancelledError``，裸 ``while`` 就退化成满速空转；
    清理任务本身卡住时则反过来变成永不返回。到点主动 cancel 后收尾。
    """
    cancelled = False
    deadline = time.monotonic() + _CLEANUP_SHIELD_TIMEOUT
    while not cleanup_task.done():
        if time.monotonic() >= deadline:
            cleanup_task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.shield(cleanup_task)
            break
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled = True
            continue

    # 清理失败必须显式面上来，不能被伪装成一次成功关闭。
    # 任务此时已 done，``result()`` 不再 await。
    cleanup_task.result()
    if cancelled:
        raise asyncio.CancelledError


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
        #: M6 空闲回收阈值（秒），与同步池同一默认值、同一语义；``<=0`` 表示不回收。
        #: 异步池此前没有这套回收，"与 :class:`ConnectionPool` 同构"这句自述因此是假的
        #: （第 26 轮 F-89）：一台长期存活的 ``AsyncTdxClient`` 会把每个槽位那条
        #: TLS 连接一直握到 ``close()`` 为止，闲置多久都不放手。
        idle_timeout: float = 300.0,
        max_retries: int = 3,
        spec: FrameSpec | None = None,
        use_tls: bool = False,
        handshake: bool | None = None,
        handshake_strict: bool = False,
    ) -> None:
        # --- fail-closed contract validation (merged from hardening) --------- #
        # 1. hosts 归一化 + family 匹配 + 去重（pool 持有快照，隔离外部修改）
        self.hosts = canonical_family_hosts(hosts, family=family)
        self.family = family
        # 2. 公共 options 校验（async_pool=True 分支）
        validate_common_pool_options(
            dict(
                slots_per_host=slots_per_host,
                timeout=timeout,
                connect_timeout=connect_timeout,
                rate_limiter=rate_limiter,
                heartbeat_interval=heartbeat_interval,
                heartbeat_cmd=heartbeat_cmd,
                max_retries=max_retries,
                use_tls=use_tls,
                handshake=handshake,
                handshake_strict=handshake_strict,
                idle_timeout=idle_timeout,
            ),
            async_pool=True,
        )

        self.handshake = (
            family in (Family.STANDARD, Family.MAC) if handshake is None else bool(handshake)
        )
        self.handshake_strict = bool(handshake_strict)
        self.slots_per_host = max(1, slots_per_host)
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.rate_limiter = rate_limiter
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_cmd = heartbeat_cmd
        self.idle_timeout = float(idle_timeout) if idle_timeout else 0.0
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
        #: 心跳/空闲回收者的唯一起跑点：池里第一次握住一条真 socket 就必须把它武装起来。
        #: 同步池在 ``__init__`` 里起跑；异步池过去只在 ``__aenter__`` 起跑，而
        #: ``AsyncTdxClient`` 家族走的是 ``open()``，从不进池的 ``async with``——于是
        #: ``idle_timeout`` / ``heartbeat_interval`` 在真实客户端路径上是幻影旋钮
        #: （第 31 轮第 2 遍实测：跑完一次请求 ``_hb is None``，``idle_timeout=0.2`` 也
        #: 不回收）。判据 tests/transport/test_async_sweeper_arm.py。
        self.start_heartbeat()
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
            await self._prune_retired_slots()

    async def _prune_retired_slots(self) -> int:
        """摘掉已排空的退役槽位；判据与理由见同步池
        :meth:`~atst.transport.pool.ConnectionPool._prune_retired_slots`（同名同口径）。
        """
        async with self._lock:
            if not self._retired_slots:
                return 0
            kept = [slot for slot in self._retired_slots if slot.leases or slot.conn is not None]
            removed = len(self._retired_slots) - len(kept)
            if removed:
                self._retired_slots = kept
            return removed

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

    # -- 熔断与运行期健康状态机（与同步池同一套语义） ---------------------- #
    async def _circuit_allows(self, host: HostEntry) -> bool:
        """单次探测放行门禁：HALF_OPEN 同期只放一个探针。"""
        async with self._lock:
            if host.circuit == "open":
                if time.time() - host.circuit_opened_at < CIRCUIT_COOLDOWN_SECONDS:
                    return False
                if host.circuit_probe_inflight:
                    return False
                host.circuit = "half_open"
                host.circuit_probe_inflight = True
                _LOG.info("异步主站 %s 熔断冷却到期，转 HALF_OPEN 放行单次探测", host.key)
                return True
            if host.circuit == "half_open":
                if host.circuit_probe_inflight:
                    return False
                host.circuit_probe_inflight = True
                return True
            return True

    async def _select_allowed_slot(
        self, *, exclude_hosts: set[str] | None = None
    ) -> AsyncSlot | None:
        """取选序后第一个被熔断状态机放行的槽位。"""
        slots = self._ordered()
        if exclude_hosts:
            preferred = [slot for slot in slots if slot.host.key not in exclude_hosts]
            candidates = preferred or slots
        else:
            candidates = slots
        for candidate in candidates:
            if await self._circuit_allows(candidate.host):
                return candidate
        return None

    async def _release_probe_token(self, slot: AsyncSlot, generation: int) -> None:
        """归还 HALF_OPEN 令牌，但不伪造任何主站健康证据。"""
        if not await self._slot_is_current(slot, generation):
            return
        async with self._lock:
            if slot.host.circuit == "half_open":
                slot.host.circuit_probe_inflight = False

    async def _mark_failure(
        self,
        slot: AsyncSlot,
        exc: BaseException,
        *,
        generation: int,
        conn: AsyncTcpConnection | None = None,
    ) -> None:
        """仅当产出该失败的代际仍是当代时才推进运行期健康。"""
        current = await self._slot_is_current(slot, generation)
        if current:
            async with self._lock:
                host = slot.host
                was_half_open = host.circuit == "half_open" or host.circuit_probe_inflight
                host.failures += 1
                if not isinstance(exc, ConnectionFailed):
                    host.biz_failures += 1
                host.consec_weighted += (
                    1.0 if isinstance(exc, ConnectionFailed) else BIZ_FAILURE_WEIGHT
                )
                host.circuit_probe_inflight = False
                if was_half_open or host.consec_weighted >= CIRCUIT_OPEN_AT:
                    if host.circuit != "open" or host.circuit_opened_at == 0.0:
                        host.circuit_opened_at = time.time()
                    host.circuit = "open"
                elif host.consec_weighted >= CIRCUIT_DEGRADED_AT:
                    host.circuit = "degraded"
                host.last_error = f"{type(exc).__name__}: {exc}"
        metrics.record_error(type(exc).__name__)
        await self._drop(slot, expected=conn)

    async def _mark_success(
        self,
        slot: AsyncSlot,
        *,
        generation: int,
        rtt_ms: float | None = None,
    ) -> None:
        """仅当代际仍有效时才清零运行期健康与熔断状态。"""
        if not await self._slot_is_current(slot, generation):
            return
        async with self._lock:
            host = slot.host
            host.failures = 0
            host.biz_failures = 0
            host.last_error = ""
            host.last_ok = time.time()
            if rtt_ms is not None:
                host.live_rtt_ms = max(0.0, float(rtt_ms))
                host.live_ok_at = host.last_ok
            host.circuit = "healthy"
            host.circuit_probe_inflight = False
            host.consec_weighted = 0.0
            host.circuit_opened_at = 0.0

    # -- 请求 --------------------------------------------------------------- #
    def _ensure_open(self) -> None:
        if self._closed:
            raise ConnectionClosed("连接池已关闭")

    async def _acquire_rate(self, timeout: float | None = None) -> None:
        """异步友好的限流取令牌。

        深审 M3：旧实现三处缺口——request 的 sleep 后**忽略**第二次
        try_acquire 结果（无论成败都放行，限流形同虚设）；request_multi /
        iter_frames 则完全绕过限流器。统一收口于此：strict 模式超速直接抛
        RateLimitedLocal（对齐同步池语义）；否则异步等待令牌，不阻塞事件循环。

        **并发契约（第 24 轮 G29 实测）**：非 strict 这一支是轮询——与同步
        :meth:`TokenBucket.acquire` 同为共享令牌池，**不保证先到先得**（异步形状副本实测
        136 对次序反转、中位等待 508 ms > 理想末位 230 ms，0.05 粒度使其更慢）。

        **等待必须有上界（2026-09-29 修）**：过去这里没有超时参数，等待发生在真正 I/O
        之前，因此完全不计入调用方的 ``request_timeout``——语义上等于"回填到取到为止"，
        速率档位被配得极低时一个协程可以永久挂住，且没有任何中断手段。现在它吃
        ``timeout``（缺省 :data:`DEFAULT_ACQUIRE_TIMEOUT`）：到点抛
        :class:`RateLimitedLocal`，与同步池同一条口径。要"超速即失败"仍配 strict。
        """
        limiter = self.rate_limiter
        if limiter is None:
            return
        if limiter.strict:
            limiter.acquire()
            return
        budget = DEFAULT_ACQUIRE_TIMEOUT if timeout is None else float(timeout)
        deadline = time.monotonic() + budget
        while not limiter.try_acquire():
            if time.monotonic() >= deadline:
                raise RateLimitedLocal(
                    f"本地限流：{limiter.state} 时段等待令牌超过 {budget:g}s"
                    f"（速率 {limiter.rate:.0f}/s）——放弃而不是无界等待",
                    context={
                        "state": limiter.state,
                        "rate": limiter.rate,
                        "timeout": budget,
                    },
                )
            await asyncio.sleep(0.05)

    async def request(
        self, method: int, body: bytes = b"", *, timeout: float | None = None
    ) -> ResponseFrame:
        """单帧请求：主站覆盖度、限流与熔断语义与同步池一致。"""
        request_timeout = _require_request_timeout(timeout)
        self._ensure_open()
        max_attempts = self.max_retries + 1
        distinct_hosts = len({slot.host.key for slot in self._slots})
        max_attempts = max(max_attempts, distinct_hosts)
        last_exc: BaseException | None = None
        tried: list[str] = []
        started = time.perf_counter()
        pending_backoff = 0.0

        for attempt in range(max_attempts):
            self._ensure_open()
            await self._acquire_rate(request_timeout)
            slot = await self._select_allowed_slot(exclude_hosts=set(tried))
            if slot is None:
                last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
                break
            # 与同步池同一条口径：冷却只兑现给刚失败过的那台，换到没试过的
            # 主站时上一轮的退避不再花调用方时间。
            if pending_backoff and slot.host.key in tried:
                await asyncio.sleep(pending_backoff)
            pending_backoff = 0.0
            tried.append(slot.host.key)

            conn: AsyncTcpConnection | None = None
            generation: int | None = None
            attempt_started = time.perf_counter()
            try:
                conn, generation = await self._acquire_lease(slot)
                try:
                    frame = await conn.request(method, body, timeout=request_timeout)
                finally:
                    await self._release_lease(slot, conn)
            except TdxError as exc:
                last_exc = exc
                await self._mark_failure(slot, exc, generation=slot.generation, conn=conn)
                advice = exc.advice
                if attempt + 1 >= max_attempts or not advice.retryable:
                    break
                if advice.switch_host:
                    _LOG.info("异步换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                    self._rotate_away(slot.host.key)
                if advice.backoff:
                    pending_backoff = retry_backoff_delay(advice.backoff, attempt)
                continue
            except asyncio.CancelledError:
                await self._release_probe_token(slot, slot.generation)
                raise
            except Exception as exc:
                last_exc = exc
                await self._mark_failure(slot, exc, generation=slot.generation, conn=conn)
                if attempt + 1 >= max_attempts:
                    break
                pending_backoff = 0.05 * (attempt + 1)
                continue
            except BaseException:
                await self._release_probe_token(slot, slot.generation)
                raise

            await self._mark_success(
                slot,
                generation=generation,
                rtt_ms=(time.perf_counter() - attempt_started) * 1000.0,
            )
            metrics.record_request(
                command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
            )
            return frame

        metrics.record_request(command=f"0x{method:04x}", ok=False)
        raise AllHostsUnreachable(
            f"所有主站均不可达（已尝试 {tried}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
            context={
                "hosts": tried,
                "method": hex(method),
                "attempts": max_attempts,
                "last_error": str(last_exc) if last_exc else None,
            },
            cause=last_exc,
        )

    def _rotate_away(self, host_key: str) -> None:
        for i, slot in enumerate(self._slots):
            if slot.host.key != host_key:
                self._rr = i
                return

    # -- bestip 热更新（镜像同步版） ----------------------------------------- #

    async def _drain_retired_slots(self, slots: list[AsyncSlot]) -> None:
        for slot in slots:
            await self._drop(slot)

    async def update_hosts(self, hosts: Sequence[HostEntry]) -> list[HostEntry]:
        """发布一次主站代际；相对取消保持原子性。

        ``bestip`` 测速后调用。整段替换在持有池锁 + 全部槽位锁时完成，锁序恒为
        pool._lock → slot.lock（与 :meth:`_drop` 一致），且提交段内不再 await，
        因此并发 :meth:`close` 与取消只能观察到完整的旧代或完整的新代。
        """
        self._ensure_open()
        if not hosts:
            return list(self.hosts)
        observed = validate_host_updates(hosts, family=self.family)
        to_close: list[AsyncSlot] = []

        async with self._lock:
            self._ensure_open()
            old_slots = list(self._slots)
            locked: list[AsyncSlot] = []
            try:
                for old in old_slots:
                    await old.lock.acquire()
                    locked.append(old)

                # close() 无法在本池锁持有期间提交；仍在全部 await 之后再复查一次
                # 事务边界，防止后续重构把可观察状态变更挪到边界之前。
                self._ensure_open()
                old_by_slot_key = {slot.key: slot for slot in old_slots}
                old_host_by_key = {slot.host.key: slot.host for slot in old_slots}
                generation = self._generation + 1

                published_hosts: list[HostEntry] = []
                for item in observed:
                    old_host = old_host_by_key.get(item.key)
                    published_hosts.append(
                        next_generation_host(old_host, item)
                        if old_host is not None
                        else new_endpoint_entry(item, family=self.family)
                    )

                new_slots: list[AsyncSlot] = []
                reuse: list[tuple[AsyncSlot, HostEntry]] = []
                retire: list[AsyncSlot] = []
                for host in published_hosts:
                    for index in range(self.slots_per_host):
                        key = f"{host.key}#{index}"
                        existing = old_by_slot_key.get(key)
                        if existing is None:
                            new_slots.append(
                                AsyncSlot(host=host, index=index, generation=generation)
                            )
                            continue
                        if existing.leases == 0 and not existing.retired:
                            reuse.append((existing, host))
                            new_slots.append(existing)
                        else:
                            retire.append(existing)
                            new_slots.append(
                                AsyncSlot(host=host, index=index, generation=generation)
                            )

                new_keys = {slot.key for slot in new_slots}
                for old in old_slots:
                    if old.key in new_keys:
                        continue
                    retire.append(old)
                    if old.leases == 0:
                        to_close.append(old)

                self._generation = generation
                for old, host in reuse:
                    old.host = host
                    old.generation = generation
                for old in retire:
                    old.retired = True
                    if not any(item is old for item in self._retired_slots):
                        self._retired_slots.append(old)
                self.hosts = published_hosts
                self._slots = new_slots
                self._rr = 0
            finally:
                for old in reversed(locked):
                    old.lock.release()

        if to_close:
            cleanup_task = asyncio.create_task(
                self._drain_retired_slots(to_close),
                name="atst-pool-update-cleanup",
            )
            await _await_cleanup_before_cancellation(cleanup_task)
            await self._prune_retired_slots()
        return published_hosts

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
        """多帧请求：与同步池同一套截断判定与熔断安全。

        asyncio.Lock 不可重入：持锁续帧期间必须走 ``_request_locked`` /
        ``_read_frame_locked``（``conn.request`` 会再次取锁 → 自死锁）。
        弃连（``_drop`` 需取 slot.lock）延迟到连接锁外，锁序恒为 slot → conn。
        """
        frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
        if record_size is not None:
            _require_positive_frame_limit(record_size, field="record_size")
        expect_count_enabled = _require_bool_option(expect_count, field="expect_count")
        request_timeout = _require_request_timeout(timeout)
        self._ensure_open()
        await self._acquire_rate(request_timeout)
        slot = await self._select_allowed_slot()
        if slot is None:
            raise _circuit_unreachable(method)

        conn: AsyncTcpConnection | None = None
        generation: int | None = None
        started = time.perf_counter()
        first_exc: TdxError | None = None
        cont_exc: TdxError | None = None
        result: ResponseFrame | None = None

        try:
            conn, generation = await self._acquire_lease(slot)
            try:
                async with conn._lock:
                    try:
                        first = await conn._request_locked(method, body, timeout=request_timeout)
                    except TdxError as exc:
                        first_exc = exc
                    else:
                        payload = first.payload
                        if not expect_count_enabled or len(payload) < 2:
                            result = first
                        else:
                            count = int.from_bytes(payload[:2], "little")
                            chunks = [payload[2:]]
                            got = len(chunks[0])
                            if count > 0 and record_size is None and got > 0:
                                record_size = max(1, got // count)
                            need = count * (record_size or 1) if record_size else None
                            read = 1
                            while need is not None and got < need and read < frame_limit:
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
                            if need is not None and got < need and cont_exc is None:
                                cont_exc = FramingError(
                                    "request_multi 响应截断: "
                                    f"need={need} got={got} max_frames={frame_limit}",
                                    context={
                                        "method": hex(method),
                                        "need": need,
                                        "got": got,
                                        "max_frames": frame_limit,
                                    },
                                )
                            joined = b"".join(chunks)
                            result = ResponseFrame(
                                magic=first.magic,
                                zip_flag=first.zip_flag,
                                seq=first.seq,
                                method=first.method,
                                zip_size=len(joined),
                                unzip_size=len(joined),
                                body=b"",
                                payload=joined,
                                header_raw=first.header_raw,
                            )
            finally:
                await self._release_lease(slot, conn)
        except asyncio.CancelledError:
            await self._release_probe_token(slot, slot.generation)
            await self._drop(slot, expected=conn)
            raise
        except BaseException:
            await self._release_probe_token(slot, slot.generation)
            await self._drop(slot, expected=conn)
            raise

        if first_exc is not None:
            await self._mark_failure(slot, first_exc, generation=generation, conn=conn)
            raise first_exc
        if cont_exc is not None:
            # 续帧中断一律弃连：残留半帧会污染该槽位的下一个请求（经典半包）。
            _LOG.warning("异步 request_multi 数据不完整，返回已收到前缀并弃连: %s", cont_exc)
            await self._mark_failure(slot, cont_exc, generation=generation, conn=conn)
            metrics.record_request(
                command=f"0x{method:04x}",
                ok=False,
                duration=time.perf_counter() - started,
            )
            assert result is not None
            return result

        await self._mark_success(
            slot,
            generation=generation,
            rtt_ms=(time.perf_counter() - started) * 1000.0,
        )
        metrics.record_request(
            command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
        )
        assert result is not None
        return result

    async def iter_frames(
        self, method: int, body: bytes = b"", *, max_frames: int = 512
    ) -> AsyncIterator[ResponseFrame]:
        """逐帧迭代：与同步池同一套上限校验与脏 socket 处置。

        整体租约：seq 分配、写帧、逐帧读取全程持连接锁；逐帧 touch last_used
        让心跳的空闲判定对活跃流让路。消费方弃读（GeneratorExit / aclose）时
        socket 内残留未读帧，连接不可复用——弃连。
        """
        frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
        self._ensure_open()
        await self._acquire_rate()
        slot = await self._select_allowed_slot()
        if slot is None:
            raise _circuit_unreachable(method)

        conn: AsyncTcpConnection | None = None
        generation: int | None = None
        failed: TdxError | None = None
        frames_read = 0
        try:
            conn, generation = await self._acquire_lease(slot)
            try:
                async with conn._lock:
                    frame_bytes, _ = build_request(
                        method,
                        body,
                        seq=conn.next_seq(),
                        spec=conn.spec,
                    )
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
                        while frames_read < frame_limit:
                            try:
                                frame = await conn._read_frame_locked()
                            except TdxError as exc:
                                failed = exc
                                break
                            frames_read += 1
                            conn.stats.last_used = time.time()
                            yield frame
            finally:
                await self._release_lease(slot, conn)
        except asyncio.CancelledError:
            await self._release_probe_token(slot, slot.generation)
            await self._drop(slot, expected=conn)
            raise
        except BaseException:
            await self._release_probe_token(slot, slot.generation)
            await self._drop(slot, expected=conn)
            raise

        if failed is not None:
            _LOG.warning("异步 iter_frames 中断（%s: %s），弃连", type(failed).__name__, failed)
            await self._mark_failure(slot, failed, generation=generation, conn=conn)
            return

        await self._mark_success(slot, generation=generation)
        # 命中调用方上限并不能证明服务端流已结束：socket 里可能还有本次请求的
        # 未读帧，这种连接绝不能归还给池复用。
        if frames_read >= frame_limit or self._closed:
            await self._drop(slot, expected=conn)

    # -- 生命周期 ----------------------------------------------------------- #
    async def _sweep_idle(self) -> int:
        """M6：回收闲置连接，与同步池 :meth:`~atst.transport.pool.ConnectionPool._sweep_idle`
        同一判据、同一把锁序（先 ``slot.lock`` 再 ``conn._lock``），只有一条 in-flight
        请求都没占用的槽位才可能被扫走。
        """
        if self.idle_timeout <= 0:
            return 0
        cutoff = time.time() - self.idle_timeout
        reclaimed = 0
        for slot in list(self._slots):
            async with slot.lock:
                conn = slot.conn
                if conn is None or not conn.connected or slot.leases or slot.retired:
                    continue
                last = conn.stats.last_used or conn.stats.created_at
                if not last or last >= cutoff:
                    continue
                async with conn._lock:
                    with contextlib.suppress(Exception):
                        await conn.close()
                if slot.conn is conn:
                    slot.conn = None
                reclaimed += 1
        if reclaimed:
            _LOG.info(
                "M6 空闲回收（异步池）：关闭 %d 个闲置连接（%ds 未使用）",
                reclaimed,
                self.idle_timeout,
            )
        return reclaimed

    async def _heartbeat_loop(self) -> None:
        """心跳镜像：喂给同一套熔断/运行期健康状态机。

        这个循环同时是异步池 **M6 空闲回收** 的唯一执行方，所以它的启动条件不能只看
        ``heartbeat_interval``——与同步池第 26 轮 F-88 那条同一口径：把心跳调成 0
        （合法取值，意为"不主动探活"）不得顺手关掉空闲回收。
        """
        probe_interval = max(1, int(self.heartbeat_interval or 0))
        #: 唤醒节奏：心跳开着跟心跳，只开回收时跟回收阈值的四分之一（不早于 1 秒）。
        tick = float(probe_interval) if self.heartbeat_interval else max(1.0, self.idle_timeout / 4)
        while not self._closed:
            await asyncio.sleep(tick)
            if self._closed:
                return
            await self._sweep_idle()
            if not self.heartbeat_interval:
                continue
            for slot in list(self._slots):
                if self._closed:
                    return
                if not await self._circuit_allows(slot.host):
                    continue
                # 每轮从 slot 重取 conn 引用，绝不跨 await 持旧引用做决策——
                # ping 挂起期间该槽位可能已被请求路径弃连并重建。
                conn: AsyncTcpConnection | None = None
                generation: int | None = None
                try:
                    conn, generation = await self._acquire_lease(slot)
                    idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                    if idle < probe_interval:
                        await self._release_lease(slot, conn)
                        await self._release_probe_token(slot, slot.generation)
                        continue
                    try:
                        rtt = await conn.ping(self.heartbeat_cmd)
                    finally:
                        await self._release_lease(slot, conn)
                except asyncio.CancelledError:
                    await self._release_probe_token(slot, slot.generation)
                    raise
                except Exception as exc:
                    await self._mark_failure(slot, exc, generation=slot.generation, conn=conn)
                except BaseException:
                    await self._release_probe_token(slot, slot.generation)
                    raise
                else:
                    await self._mark_success(slot, generation=generation, rtt_ms=rtt)

    def start_heartbeat(self) -> None:
        if (self.heartbeat_interval or self.idle_timeout > 0) and self._hb is None:
            # T5：get_event_loop() 在 3.12+ 的运行中协程里已弃用/报错，
            # 统一用 get_running_loop()。
            self._hb = asyncio.get_running_loop().create_task(self._heartbeat_loop())

    async def _cleanup_committed_close(
        self, *, heartbeat: asyncio.Task | None, slots: list[AsyncSlot]
    ) -> None:
        """排空全部关停资源，然后把首个清理失败面上来。"""
        first_error: BaseException | None = None
        current = asyncio.current_task()
        if heartbeat is not None and heartbeat is not current:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                first_error = exc

        for slot in slots:
            try:
                await self._drop(slot)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error

    async def close(self) -> None:
        """取消安全的三段式关停。

        1. 在旧池状态完全不动的前提下取到当前 + 已退役的全部槽位锁；
        2. 提交关闭代际——提交段内没有 await，取消只能观察到完整旧代或完整新代；
        3. 取消心跳并排空每条连接，再传播取消或任何清理失败。

        重复 ``close()`` 依旧执行清理：已关闭是资源状态，不是跳过幂等排空的理由。
        """
        slots: list[AsyncSlot]
        heartbeat: asyncio.Task | None

        async with self._lock:
            slots = _unique_slots(self)
            locked: list[AsyncSlot] = []
            try:
                # 准备阶段可安全取消：拿到全部锁之前不改任何池/槽位状态。
                for slot in slots:
                    await slot.lock.acquire()
                    locked.append(slot)

                if not self._closed:
                    self._closed = True
                    self._generation += 1
                for slot in slots:
                    slot.retired = True
                heartbeat = self._hb
                self._hb = None
            finally:
                for slot in reversed(locked):
                    slot.lock.release()

        cleanup_task = asyncio.create_task(
            self._cleanup_committed_close(heartbeat=heartbeat, slots=slots),
            name="atst-pool-close-cleanup",
        )
        await _await_cleanup_before_cancellation(cleanup_task)

    async def __aenter__(self) -> AsyncConnectionPool:
        #: 起跑点不在这里：唯一一处住在 ``_get_conn_locked``（第一次握住真 socket 时武装）。
        #: 从 ``__aenter__`` 搬走是因为 ``AsyncTdxClient`` 家族走 ``open()``，从不进这道门，
        #: 于是 ``idle_timeout`` / ``heartbeat_interval`` 在 shipped 异步面上是幻影旋钮
        #: （G47，判据 tests/transport/test_async_sweeper_arm.py）。
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
