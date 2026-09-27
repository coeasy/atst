# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步 TCP 连接（§12.2）。

职责边界
--------
``TcpConnection`` **只管字节**：建连、发帧、收帧、解压、校验 seq。
它不理解任何业务语义（K 线 / 行情 / 财务），那属于
:mod:`tstdx.client` 与 :mod:`tstdx.protocol`。

可靠性设计
----------
* **精确长度读取**：``_recv_exact`` 循环读满，杜绝"半包"。
* **seq 校验**：请求序号不匹配即视为流错位，立即断连重建（不改业务层）。
* **半开连接探测**：``recv`` 返回空字节 → :class:`ConnectionClosed`。
* **超时分层**：建连超时与读写超时可分别设置（建连通常应更短）。
* **TCP keepalive**：尽量开启，让 OS 帮忙发现死连接。
"""

from __future__ import annotations

import contextlib
import logging
import socket
import ssl
import threading
import time
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
    ConnectionClosed,
    ConnectionFailed,
    DecompressError,
    FramingError,
    ProtocolError,
    ReadTimeout,
    TdxError,
    WriteTimeout,
)
from ..protocol.commands import Family
from ._validation import (
    require_bool,
    require_timeout,
    validate_common_connection_args,
)

__all__ = [
    "TcpConnection",
    "ConnectionStats",
    "DEFAULT_HEARTBEAT_CMD",
]

#: 传输域统一 logger（只获取，不配置 handler——配置交给宿主应用）。
_LOG = logging.getLogger("tstdx.transport")

#: 心跳探测命令：探活只判传输是否通畅，**不解析响应**。
#: 2026-09-26 真机实测（7/7 可达主站一致）把旧注释的理由否证了：这里并非"服务端对
#: 未知命令回一个短帧"——0x0002 回 50 字节（含 GBK 文本「上交所公告」，布局未锁定），
#: 账本里登记为 HEARTBEAT 的 0x0004 回 10 字节无结构载荷（``0000000000003c283501``）。
#: 两条都答，换谁都不影响探活正确性，故保持 0x0002 不动（台账 F-20；字节证据见
#: ``PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml`` 的 ``measured`` 块）。
#: 两码的账本地位不同：0x0002 **未**登记在 7709 标准族账本，0x0004 登记了但本包没有
#: 默认发送方，只有 ``tstdx probe 0x0004`` 会显式把它发出去。
#: 覆盖点是连接池构造参数 ``heartbeat_cmd``（``ConnectionPool`` /
#: ``AsyncConnectionPool``）；配置面**没有**对应键，`tstdx.toml` 写它不会生效。
DEFAULT_HEARTBEAT_CMD = 0x0002

_RECV_CHUNK = 65536


# --------------------------------------------------------------------------- #
# 统计
# --------------------------------------------------------------------------- #
@dataclass
class ConnectionStats:
    """连接级计数器（供可观测性与故障转移决策使用）。"""

    requests: int = 0
    failures: int = 0
    bytes_sent: int = 0
    bytes_recv: int = 0
    reconnects: int = 0
    created_at: float = field(default_factory=time.time)
    last_used: float = 0.0
    last_error: str = ""

    @property
    def error_rate(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.failures / self.requests

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["error_rate"] = round(self.error_rate, 4)
        return d


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
class TcpConnection:
    """一条到 TDX 主站的 TCP 连接。

    Example
    -------
    >>> conn = TcpConnection("119.147.212.81", 7709, timeout=3.0)
    >>> conn.connect()
    >>> frame = conn.request(0x044E, struct.pack("<H", 0))
    >>> conn.close()
    """

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
        keepalive: bool = True,
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
            keepalive=keepalive,
        )
        self.tls_context = tls_context
        self.spec = spec
        self.keepalive = require_bool("keepalive", keepalive)
        self.connect_timeout = (
            self.connect_timeout if self.connect_timeout is not None else self.timeout
        )

        self._sock: socket.socket | None = None
        self._seq = 0
        self._handshaked = False
        self.stats = ConnectionStats()
        # 连接级租约锁（C2）：一条 TCP 连接同一时刻只允许一个「请求会话」，
        # 覆盖 connect → seq 分配 → 发帧 → 收帧（含 request_multi 续帧）全流程。
        # 必须可重入（RLock）：request/ping 持锁期间会经 connect/_send_setup
        # 再次进入 read_frame/_sendall 等加锁入口。
        self._lock = threading.RLock()

    # -- 属性 --------------------------------------------------------------- #
    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @property
    def fileno(self) -> int:
        return self._sock.fileno() if self._sock is not None else -1

    def __repr__(self) -> str:  # pragma: no cover
        state = "up" if self.connected else "down"
        return f"<TcpConnection {self.host}:{self.port}#{self.slot_id} {state}>"

    # -- 生命周期 ----------------------------------------------------------- #
    def connect(self) -> TcpConnection:
        # T5（TOCTOU）：检查与建连必须在同一临界区内完成。旧实现
        # ``if self._sock is not None: return`` 在锁外判断，两个线程同时
        # 看到 _sock=None 会各自建连，先建的那条 socket 永久泄漏。
        with self._lock:
            if self._sock is not None:
                return self
            try:
                sock = socket.create_connection(self.addr, timeout=self.connect_timeout)
            except (TimeoutError, socket.timeout) as exc:  # noqa: UP041 - 显式双分支保 3.9+ 兼容（T1）
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
                sock.settimeout(self.timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if self.keepalive:
                    self._enable_keepalive(sock)
                if self.use_tls:
                    ctx = self.tls_context or ssl.create_default_context()
                    sock = ctx.wrap_socket(sock, server_hostname=self.host)
            except Exception:
                with contextlib.suppress(Exception):
                    sock.close()
                raise

            self._sock = sock
            self._seq = 0
            self._handshaked = False
            self.stats.reconnects += 1

            # 握手是「会话」而非「进程」级状态：每次（重）连都必须重发。
            # 缺失握手 → 服务端静默不回 → 读超时 → 断连 → 重连 → 又不握手，
            # 形成级联超时。把它放在 connect() 里可从根上消除该故障模式。
            if self.handshake:
                self._send_setup()
            return self

    # -- 握手 --------------------------------------------------------------- #
    def _send_setup(self) -> None:
        """发送握手帧并丢弃响应。

        握手失败处理见 :attr:`handshake_strict`：
        默认仅记录告警（部分主站不要求握手）；严格模式直接让建连失败。
        """
        from ..protocol.handshake import setup_frames

        frames = setup_frames(self.family, blob=self.handshake_blob)
        if not frames:
            self._handshaked = True
            return
        for raw in frames:
            try:
                self._sendall(raw)
                self.read_frame()
            except TdxError as exc:
                self._handshaked = False
                self.stats.last_error = f"handshake: {type(exc).__name__}: {exc}"
                if self.handshake_strict:
                    self.close()
                    raise
                # 容忍：放弃剩余握手帧，让业务请求照常走，
                # 若服务端确实需要握手，业务请求会失败并触发标准重试路径。
                _LOG.warning(
                    "握手失败（容忍模式，业务请求继续）%s:%s: %s",
                    self.host,
                    self.port,
                    exc,
                )
                return
        self._handshaked = True
        self._seq = 0

    @staticmethod
    def _enable_keepalive(sock: socket.socket) -> None:
        """开启 TCP keepalive。各平台常量名不同，缺哪个就跳过哪个。"""
        try:
            sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_KEEPALIVE", 9), 1)
        except OSError:
            return
        for opt, val in (
            ("TCP_KEEPIDLE", 30),
            ("TCP_KEEPINTVL", 10),
            ("TCP_KEEPCNT", 3),
        ):
            const = getattr(socket, opt, None)
            if const is None:
                const = getattr(socket, opt.replace("TCP_", "SO_"), None)
            if const is None:
                continue
            try:
                sock.setsockopt(socket.IPPROTO_TCP, const, val)
            except OSError:
                continue
        # Windows：SIO_KEEPALIVE_VALS 需要 ioctlsocket，标准库无直接接口，跳过

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    # -- 上下文管理 --------------------------------------------------------- #
    def __enter__(self) -> TcpConnection:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 帧 IO -------------------------------------------------------------- #
    def _require(self) -> socket.socket:
        if self._sock is None:
            raise ConnectionClosed(
                f"连接已关闭: {self.host}:{self.port}",
                context={"host": self.host, "port": self.port},
            )
        return self._sock

    def _recv_exact(self, size: int) -> bytes:
        sock = self._require()
        chunks: list[bytes] = []
        remaining = size
        #: 墙钟截止。``sock.settimeout`` 量的是**单次 recv 的空档**，每收到 1 字节就重新
        #: 武装，所以逐字节吐数据的对端永远撞不到它：声明 ``timeout=0.4s`` 的调用方在
        #: 第 31 轮第 2 遍实测里读 ``_recv_exact(32768)``（32768 = 单帧上限，由对端的
        #: zip_size 决定）始终没有返回。池的重试上界按 attempt 计，每个 attempt 本身
        #: 无界，兜不住这一格。异步孪生用一条 ``wait_for`` 包住整个 ``readexactly``，
        #: 本来就有墙钟——同步面在这一刀之前严格更弱。
        deadline = time.monotonic() + self.timeout
        while remaining > 0:
            left = deadline - time.monotonic()
            if left <= 0:
                self.close()
                raise ReadTimeout(
                    f"读取超时({self.timeout}s): 还需 {remaining} 字节",
                    context={"host": self.host, "port": self.port, "remaining": remaining},
                )
            #: 只在预算比声明超时更窄时收紧，默认路径逐字节不变。
            sock.settimeout(min(self.timeout, left))
            try:
                chunk = sock.recv(min(_RECV_CHUNK, remaining))
            except (TimeoutError, socket.timeout) as exc:  # noqa: UP041 - 显式双分支保 3.9+ 兼容（T1）
                # T1 跨版本归类：3.10+ socket.timeout 即 TimeoutError；
                # 3.9- 两者不同且 socket.timeout 是 OSError 子类，显式列出
                # 保证归类为 ReadTimeout（而非误入 OSError 分支变成
                # ConnectionClosed），ReadTimeout 的 advice 才能被消费。
                #: 这一分支不 close（与异步孪生同形：调用方自己决定丢不丢这条连接），
                #: 所以循环里为墙钟收紧出来的 ``min(self.timeout, left)`` 必须由这里复原——
                #: 否则残留的那点余量会交给下一条帧读取，读预算凭空变成几十毫秒。
                sock.settimeout(self.timeout)
                raise ReadTimeout(
                    f"读取超时({self.timeout}s): 还需 {remaining} 字节",
                    context={"host": self.host, "port": self.port, "remaining": remaining},
                    cause=exc,
                ) from exc
            except OSError as exc:
                self.close()
                raise ConnectionClosed(
                    f"读取失败: {exc}",
                    context={"host": self.host, "port": self.port},
                    cause=exc,
                ) from exc
            if not chunk:
                self.close()
                raise ConnectionClosed(
                    "对端关闭连接（半开连接）",
                    context={"host": self.host, "port": self.port},
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        #: 读满即复原：循环里为了墙钟截止可能把 socket 调得比 ``self.timeout`` 更紧，
        #: 而这条连接接下来会被复用（下一帧、下一个请求），不复原就把一次成功读取的
        #: 残余预算留给后来者。剩下的三条失败路径里，``close()`` 掉连接的两格不必复原，
        #: 唯一留着连接的那格（逐次空档超时）在抛错前自己复原了。
        sock.settimeout(self.timeout)
        data = b"".join(chunks)
        self.stats.bytes_recv += len(data)
        return data

    def _sendall(self, data: bytes) -> None:
        sock = self._require()
        try:
            sock.sendall(data)
        except (TimeoutError, socket.timeout) as exc:  # noqa: UP041 - 显式双分支保 3.9+ 兼容（T1）
            # T1 跨版本归类：同 _recv_exact，保证 WriteTimeout（而非
            # ConnectionClosed）advice 被消费。
            raise WriteTimeout(
                f"发送超时({self.timeout}s)",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc
        except OSError as exc:
            self.close()
            raise ConnectionClosed(
                f"发送失败: {exc}",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc
        self.stats.bytes_sent += len(data)

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq

    def read_frame(self, timeout: float | None = None) -> ResponseFrame:
        """读取并解码一个完整响应帧。

        C2：读帧全程持连接锁——request_multi 的续帧读取在锁外调用本方法
        时逐帧原子；与在飞 request/心跳 ping 天然串行。

        `timeout` 与 :meth:`request` 的同名参数同义：只对本次读帧生效，
        缺省沿用连接自身的 :attr:`timeout`。持锁期间临时改 socket 超时、
        退出时复原，因此调用方给的截止值真的会传到 socket 上。
        """
        with self._lock:
            sock = self._require()
            old_timeout: float | None = None
            if timeout is not None and abs(float(timeout) - self.timeout) > 1e-9:
                old_timeout = self.timeout
                self.timeout = float(timeout)
                sock.settimeout(self.timeout)
            try:
                return self._read_frame_unlocked()
            finally:
                if old_timeout is not None:
                    self.timeout = old_timeout
                    if self._sock is not None:
                        self._sock.settimeout(old_timeout)

    def _read_frame_unlocked(self) -> ResponseFrame:
        """read_frame 的无锁核心（调用方必须已持有 ``self._lock``）。"""
        header = self._recv_exact(self.spec.resp_header_size)
        frame = parse_response_header(header, self.spec)
        if frame.magic != self.spec.magic:
            raise FramingError(
                f"magic 不匹配: 收到 {hex(frame.magic)}，期望 {hex(self.spec.magic)}",
                context={"host": self.host, "port": self.port},
            )
        if frame.zip_size > self.spec.max_frame_bytes:
            raise FramingError(
                f"响应帧过大: {frame.zip_size} > {self.spec.max_frame_bytes}",
                context={"zip_size": frame.zip_size},
            )
        body = self._recv_exact(frame.zip_size)
        try:
            return decode_response_body(frame, body, strict=True)
        except DecompressError:
            raise
        except FramingError as exc:
            raise ProtocolError(
                f"响应帧长度自洽性校验失败: {exc}",
                context={"host": self.host, "port": self.port},
                cause=exc,
            ) from exc

    # -- 请求 --------------------------------------------------------------- #
    def request(
        self,
        method: int,
        body: bytes = b"",
        *,
        check_seq: bool = True,
        compress: bool = False,
        timeout: float | None = None,
    ) -> ResponseFrame:
        """发一帧、收一帧。

        Raises
        ------
        传输层异常统一为 :class:`~tstdx.errors.TransportError` 子类，
        其 :attr:`~tstdx.errors.TdxError.advice` 指示是否应重试 / 换主站 / 降级。
        """
        # --- fail-closed contract validation (merged from hardening) --------- #
        require_bool("check_seq", check_seq)
        require_bool("compress", compress)
        timeout = require_timeout("request.timeout", timeout, allow_none=True)
        # C2 连接级租约：整个「建连 → seq → 发帧 → 收帧 → seq 校验」会话
        # 在同一临界区内完成，杜绝同 socket 帧交织。
        with self._lock:
            if self._sock is None:
                self.connect()

            old_timeout: float | None = None
            if timeout is not None and abs(timeout - self.timeout) > 1e-9:
                old_timeout = self.timeout
                self.timeout = float(timeout)
                self._require().settimeout(self.timeout)

            seq = self.next_seq()
            frame_bytes, seq = build_request(
                method, body, seq=seq, spec=self.spec, compress=compress
            )
            self.stats.requests += 1
            self.stats.last_used = time.time()
            try:
                self._sendall(frame_bytes)
                frame = self.read_frame()
            except TdxError as exc:
                self.stats.failures += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                # 传输层错误一律断连，避免复用处于未知状态的 socket
                self.close()
                raise
            except Exception:  # pragma: no cover - 兜底
                self.stats.failures += 1
                self.close()
                raise
            finally:
                if old_timeout is not None:
                    self.timeout = old_timeout
                    if self._sock is not None:
                        self._sock.settimeout(old_timeout)

            if check_seq and frame.seq != seq:
                self.close()
                self.stats.failures += 1
                raise ProtocolError(
                    f"响应 seq 不匹配: 期望 {seq}，收到 {frame.seq}（流已错位，连接已重建）",
                    context={"expect": seq, "got": frame.seq, "method": hex(method)},
                )
            return frame

    # -- 探活 --------------------------------------------------------------- #
    def ping(self, cmd: int = DEFAULT_HEARTBEAT_CMD, body: bytes = b"") -> float:
        """发送探活帧，返回往返耗时（毫秒）。

        只判断传输是否通畅，**不校验响应内容**——这样探活命令的选择
        不会因主站实现差异而失效。

        C2：全程持连接锁。池心跳在锁外等待本锁，探活与在飞请求天然串行，
        不再出现心跳帧插入请求读序的交叉。
        """
        with self._lock:
            if self._sock is None:
                self.connect()
            frame_bytes, _ = build_request(cmd, body, seq=self.next_seq(), spec=self.spec)
            started = time.perf_counter()
            try:
                self._sendall(frame_bytes)
                header = self._recv_exact(self.spec.resp_header_size)
                frame = parse_response_header(header, self.spec)
                if frame.zip_size:
                    # P1：对齐 read_frame 的 max_frame_bytes 校验——错位流下的
                    # 垃圾 zip_size 不得触发无上限长读。
                    if frame.zip_size > self.spec.max_frame_bytes:
                        raise FramingError(
                            f"探活响应帧过大: {frame.zip_size} > {self.spec.max_frame_bytes}",
                            context={"zip_size": frame.zip_size},
                        )
                    self._recv_exact(frame.zip_size)
            except TdxError:
                self.close()
                raise
            except Exception as exc:
                self.close()
                raise ConnectionClosed(
                    f"探活失败: {exc}", context={"host": self.host, "port": self.port}, cause=exc
                ) from exc
            return (time.perf_counter() - started) * 1000.0

    # -- 诊断 --------------------------------------------------------------- #
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
