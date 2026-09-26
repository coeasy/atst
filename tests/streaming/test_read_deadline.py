# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""读帧截止值的贯通判据（第 28 轮第 1 遍）。

起因：``PushChannel.read(timeout=5.0)`` 的截止值在真实连接上从未生效——
``_read_frame`` 用 ``except TypeError`` 退回到无参 ``read_frame()``，把调用方
给的数丢掉；而真连接抛的是 :class:`~tstdx.errors.ReadTimeout` /
:class:`~tstdx.errors.ConnectionClosed`（``TdxError`` 支系，不是 ``OSError``），
``_read_with_reason`` 的 ``except TimeoutError`` / ``except OSError`` 两格在真
连接上永远进不去——「超时返回 None」和「断线记 last_error」只剩测试替身版本。

这里量的不是替身，而是四件事：两张连接的签名收这个关键字、真 socket 上它真的
截止读帧、PushChannel 把真连接的异常归类成文档写的理由、退回无参那条吞异常
路径不许回来。
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import socket
import threading
import time
from pathlib import Path

import pytest

from tstdx.errors import ConnectionClosed, FramingError, ReadTimeout
from tstdx.streaming.push import PushChannel
from tstdx.transport.async_ import AsyncTcpConnection
from tstdx.transport.base import TcpConnection


def _timeout_param(fn: object) -> inspect.Parameter:
    params = inspect.signature(fn).parameters  # type: ignore[arg-type]
    assert "timeout" in params, f"{getattr(fn, '__qualname__', fn)} 不接受 timeout 参数"
    return params["timeout"]


class TestReadFrameAcceptsDeadline:
    """读帧入口必须像 ``request`` 一样接受逐次截止值——否则调用方的参数无处可去。"""

    def test_sync_read_frame_has_a_timeout_parameter(self) -> None:
        assert _timeout_param(TcpConnection.read_frame).name == "timeout"

    def test_async_read_frame_has_a_timeout_parameter(self) -> None:
        assert _timeout_param(AsyncTcpConnection.read_frame).name == "timeout"

    def test_request_and_read_frame_share_the_deadline_vocabulary(self) -> None:
        """两个入口都得有 timeout：只有一处有，就是「参数走到半路掉了」。"""
        _timeout_param(TcpConnection.request)
        _timeout_param(TcpConnection.read_frame)
        _timeout_param(AsyncTcpConnection.request)
        _timeout_param(AsyncTcpConnection.read_frame)


class _SilentServer:
    """本机回环上一个「接受连接但永远不回字节」的服务端。"""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port: int = self._sock.getsockname()[1]
        self._accepted: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._accepted.append(conn)

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            self._sock.close()
        for conn in self._accepted:
            with contextlib.suppress(Exception):
                conn.close()
        self._thread.join(timeout=1.0)


class TestDeadlineReachesTheSocket:
    """真 socket 上的实测：截止值必须真的结束这次读帧，而不是只挂在签名里。"""

    @pytest.mark.parametrize("timeout", [0.05, 0.2])
    def test_read_frame_gives_up_within_the_callers_deadline(self, timeout: float) -> None:
        server = _SilentServer()
        # 连接自身的超时给得很大：唯一能让这次读帧早些结束的就是调用方的截止值。
        conn = TcpConnection("127.0.0.1", server.port, timeout=30.0, handshake=False)
        try:
            conn.connect()
            started = time.monotonic()
            with pytest.raises(ReadTimeout):
                conn.read_frame(timeout=timeout)
            elapsed = time.monotonic() - started
            assert elapsed < timeout + 1.0, f"截止值没生效：等了 {elapsed:.2f}s"
        finally:
            conn.close()
            server.close()

    def test_default_still_uses_the_connection_timeout(self) -> None:
        """不传截止值时行为不变——用连接自己的 timeout，且读帧后属性不被改坏。"""
        server = _SilentServer()
        conn = TcpConnection("127.0.0.1", server.port, timeout=0.05, handshake=False)
        try:
            conn.connect()
            with pytest.raises(ReadTimeout):
                conn.read_frame()
            assert conn.timeout == 0.05
        finally:
            conn.close()
            server.close()

    def test_per_call_deadline_does_not_leak_onto_the_connection(self) -> None:
        """一次带截止值的读帧之后，连接属性必须回到原值（与 request 的换值复原同语义）。"""
        server = _SilentServer()
        conn = TcpConnection("127.0.0.1", server.port, timeout=3.0, handshake=False)
        try:
            conn.connect()
            with pytest.raises(ReadTimeout):
                conn.read_frame(timeout=0.05)
            assert conn.timeout == 3.0
            with pytest.raises(ReadTimeout) as second:
                conn.read_frame(timeout=0.05)
            assert "0.05" in str(second.value)
            assert conn.timeout == 3.0
        finally:
            conn.close()
            server.close()


class TestAsyncChainGetsTheSameDeadline:
    """两张传输链是同一条主链的两个身位：同步修好、异步漏掉，就是 B-2 那类漂移。"""

    def test_async_read_frame_honours_the_callers_deadline(self) -> None:
        import asyncio

        async def scenario() -> tuple[float, float]:
            # 只接受、永不回帧的本机监听 socket；wait_closed 在 3.12 上会等到
            # 在飞连接结束，这里不依赖它，只 close 监听口。
            server = await asyncio.start_server(lambda reader, writer: None, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            conn = AsyncTcpConnection("127.0.0.1", port, timeout=30.0, handshake=False)
            started = time.monotonic()
            try:
                await asyncio.wait_for(conn.connect(), 5.0)
                with pytest.raises(ReadTimeout):
                    await asyncio.wait_for(conn.read_frame(timeout=0.05), 5.0)
                elapsed = time.monotonic() - started
                left = conn.timeout
            finally:
                await asyncio.wait_for(conn.close(), 5.0)
                server.close()
            return elapsed, left

        elapsed, left = asyncio.run(scenario())
        assert elapsed < 1.05, f"截止值没生效：等了 {elapsed:.2f}s"
        assert left == 30.0, "换出去的超时必须在读帧结束时复原"


class _RaisingTransport:
    def __init__(self, exc: BaseException):
        self._exc = exc
        self.timeouts: list[float] = []

    def read_frame(self, timeout: float | None = None):
        self.timeouts.append(float(timeout or 0.0))
        raise self._exc


class TestRealTransportErrorsMapToDocumentedReasons:
    """真连接抛的是 TdxError 支系；这两格不认它，文档的口径就只对替身成立。"""

    def test_read_timeout_is_the_non_terminal_timeout_reason(self) -> None:
        ch = PushChannel(_RaisingTransport(ReadTimeout("读取超时(0.05s)")))
        frame, reason = ch._read_with_reason(0.05)
        assert (frame, reason) == (None, "timeout")
        assert ch.last_error is None, "超时不是断线，不该记成传输错误"

    def test_connection_closed_is_the_terminal_transport_reason(self) -> None:
        exc = ConnectionClosed("对端关闭连接（半开连接）")
        ch = PushChannel(_RaisingTransport(exc))
        frame, reason = ch._read_with_reason(1.0)
        assert (frame, reason) == (None, "transport")
        assert ch.last_error is exc, "断线必须留痕，供调用方做重连决策"

    def test_framing_damage_still_propagates(self) -> None:
        """数据损坏不许伪装成「这一轮没有帧」。"""
        ch = PushChannel(_RaisingTransport(FramingError("magic 不匹配")))
        with pytest.raises(FramingError):
            ch._read_with_reason(1.0)

    def test_caller_deadline_reaches_the_transport(self) -> None:
        t = _RaisingTransport(ReadTimeout("x"))
        PushChannel(t).read(timeout=0.25)
        assert t.timeouts == [0.25]


class TestSilentFallbackIsGone:
    """``except TypeError`` 退路是这条断链的成因：它把参数吞成 0 个执行方。"""

    def test_push_channel_read_frame_has_no_typeerror_fallback(self) -> None:
        path = Path(str(inspect.getsourcefile(PushChannel)))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_read_frame"
        )
        handlers = [
            h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler) and _names(h, "TypeError")
        ]
        assert not handlers, "_read_frame 又退回静默丢截止值的 except TypeError 了"


def _names(handler: ast.ExceptHandler, name: str) -> bool:
    if handler.type is None:
        return False
    return any(isinstance(node, ast.Name) and node.id == name for node in ast.walk(handler.type))
