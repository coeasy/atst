# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""罐头 TDX 主站（仅服务 tests/transport）。

线程化 socket server，按 :mod:`tstdx.codec.framing` 的 7709 帧布局工作：

* 请求头 12 字节 ``<BIBHHH``（zip, seq, packet_type, pkg_len1, pkg_len2, method）
* 响应头 16 字节 ``<IBIBHHH``（magic, zip_flag, seq, reserved, method, zip, unzip）

行为（默认全Echo）：

* 业务命令（如 ``ECHO_CMD``）→ 响应 ``payload = seq(4LE) + body``（回显 seq + body，
  供 ``check_seq`` 与「无一帧错位」断言使用）；
* ``0x000D`` / ``0x0FDB``（握手）与 ``HB_CMD``（心跳）→ 空 payload；
* ``MULTI_CMD`` → 首帧 ``<H count> + chunk``，后续帧为纯记录续体（可编程分帧）。

可编程行为钩子（按命令号）：``delay``（响应前睡眠）、``silent``（收帧不回）、
``close_on``（收帧即断连）、``hb_zip_size``（心跳响应头带超大 zip_size）。
server 侧按连接记录帧序（``ConnRecord.frames``）并检测错帧
（``ConnRecord.misframed``）——帧交织会立刻在 server 侧显形。
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading
import time

MAGIC = 0x0074CBB1  # DEFAULT_7709_SPEC.magic（复制常量避免 import tstdx）

REQ_HEADER = struct.Struct("<BIBHHH")  # zip, seq, ptype, pkg1, pkg2, method
RESP_HEADER = struct.Struct("<IBIBHHH")  # magic, zip_flag, seq, reserved, method, zip, unzip

#: 业务回显命令（任意合法命令号均可，传输层不解析 payload）
ECHO_CMD = 0x044E
#: 多帧续传命令：body = <III count, record_size, chunk_size>
MULTI_CMD = 0x0312
#: 心跳命令（与 transport.base.DEFAULT_HEARTBEAT_CMD 一致）
HB_CMD = 0x0002

HANDSHAKE_METHODS = (0x000D, 0x0FDB)


def multi_body(count: int, record_size: int, chunk_size: int) -> bytes:
    """构造 MULTI_CMD 请求体。"""
    return struct.pack("<III", count, record_size, chunk_size)


def multi_blob(count: int, record_size: int) -> bytes:
    """server 侧生成的确定性记录流（与解析无关，仅比对字节）。"""
    total = count * record_size
    return bytes((i * 31 + 7) & 0xFF for i in range(total))


class ConnRecord:
    """一条被接受连接的观测记录。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        #: 按到达顺序记录 (method, seq, body)
        self.frames: list[tuple[int, int, bytes]] = []
        #: 错帧描述（帧交织 / 半包在 server 侧的显形）
        self.misframed: list[str] = []
        self.sock: socket.socket | None = None

    @property
    def methods(self) -> list[int]:
        with self.lock:
            return [m for m, _seq, _b in self.frames]


class FakeTdxServer:
    """可编程罐头主站。``with`` 结束或 :meth:`close` 后停止 accept。"""

    def __init__(self) -> None:
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen(64)
        self.port: int = self._lsock.getsockname()[1]
        self.connections: list[ConnRecord] = []
        self._conn_lock = threading.Lock()
        self._stopping = False
        self._threads: list[threading.Thread] = []
        # --- 可编程行为 ---
        self.delay: dict[int, float] = {}
        self.silent: set[int] = set()
        self.close_on: set[int] = set()
        #: 正常响应后立即断连（制造「续帧中断」半包场景）
        self.close_after: set[int] = set()
        #: 置位后，心跳响应只回 16 字节头且 zip_size 为该值（不回 body）
        self.hb_zip_size: int | None = None
        self._accept = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept.start()

    # -- 观测 ---------------------------------------------------------------- #
    def conn_count(self) -> int:
        with self._conn_lock:
            return len(self.connections)

    def all_misframed(self) -> list[str]:
        with self._conn_lock:
            return [m for c in self.connections for m in c.misframed]

    def all_frames(self) -> list[tuple[int, int, bytes]]:
        with self._conn_lock:
            return [f for c in self.connections for f in list(c.frames)]

    # -- 服务循环 ------------------------------------------------------------ #
    def _accept_loop(self) -> None:
        while not self._stopping:
            try:
                sock, _addr = self._lsock.accept()
            except OSError:
                return
            record = ConnRecord()
            record.sock = sock
            with self._conn_lock:
                self.connections.append(record)
            t = threading.Thread(target=self._serve, args=(sock, record), daemon=True)
            self._threads.append(t)
            t.start()

    def _recv_exact(self, sock: socket.socket, n: int, record: ConnRecord) -> bytes | None:
        buf = b""
        while len(buf) < n:
            try:
                chunk = sock.recv(n - len(buf))
            except OSError:
                # 客户端半途断开（Windows 10054 等）属测试拆卸噪声，
                # 不计入 misframed；帧错位由坏头检测兜底。
                return None
            if not chunk:
                return None
            buf += chunk
        return buf

    def _serve(self, sock: socket.socket, record: ConnRecord) -> None:
        try:
            while True:
                header = self._recv_exact(sock, REQ_HEADER.size, record)
                if header is None:
                    return
                zip_flag, seq, ptype, pkg1, pkg2, method = REQ_HEADER.unpack(header)
                if zip_flag != 0x0C or ptype != 0x01 or pkg1 != pkg2 or pkg1 < 2:
                    # 唯一的错帧信号：读到的 12 字节不是合法请求头
                    # （半包交织 / 流错位会在这里显形）。
                    with record.lock:
                        record.misframed.append(
                            f"bad header: zip={zip_flag:#x} ptype={ptype} pkg1={pkg1} pkg2={pkg2}"
                        )
                    return
                body_len = pkg1 - 2
                body = b""
                if body_len:
                    body = self._recv_exact(sock, body_len, record)
                    if body is None:
                        return
                with record.lock:
                    record.frames.append((method, seq, body))

                if method in self.close_on:
                    sock.close()
                    return
                if method in HANDSHAKE_METHODS or method == HB_CMD:
                    if method == HB_CMD and self.hb_zip_size is not None:
                        sock.sendall(
                            RESP_HEADER.pack(
                                MAGIC, 0, seq, 0, method, self.hb_zip_size, self.hb_zip_size
                            )
                        )
                        continue
                    self._respond(sock, seq, method, b"")
                    continue
                if method in self.silent:
                    continue
                if method in self.delay:
                    time.sleep(self.delay[method])
                if method == MULTI_CMD:
                    count, record_size, chunk_size = struct.unpack("<III", body[:12])
                    self._respond_multi(sock, seq, count, record_size, chunk_size)
                    continue
                self._respond(sock, seq, method, struct.pack("<I", seq) + body)
        except OSError:
            pass  # 客户端半途断开：测试拆卸噪声，不计错帧

    def _respond(self, sock: socket.socket, seq: int, method: int, payload: bytes) -> None:
        sock.sendall(
            RESP_HEADER.pack(MAGIC, 0, seq, 0, method, len(payload), len(payload)) + payload
        )
        if method in self.close_after:
            sock.close()

    def _respond_multi(
        self, sock: socket.socket, seq: int, count: int, record_size: int, chunk_size: int
    ) -> None:
        blob = multi_blob(count, record_size)
        first = struct.pack("<H", count) + blob[:chunk_size]
        self._respond(sock, seq, MULTI_CMD, first)
        off = chunk_size
        while off < len(blob):
            self._respond(sock, seq, MULTI_CMD, blob[off : off + chunk_size])
            off += chunk_size

    # -- 生命周期 ------------------------------------------------------------ #
    def close(self) -> None:
        self._stopping = True
        with contextlib.suppress(OSError):
            self._lsock.close()
        with self._conn_lock:
            conns = list(self.connections)
        for c in conns:
            if c.sock is not None:
                with contextlib.suppress(OSError):
                    c.sock.close()

    def __enter__(self) -> FakeTdxServer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
