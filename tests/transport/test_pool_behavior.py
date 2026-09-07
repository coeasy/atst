# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F1 传输域回归（同步池）：T5 生命周期 / P1 选序与 ping 上限 / T1 超时归类 /
metrics 埋点 / 零配置日志。

全部基于罐头 server（tests/transport/fake_server.py），无外网依赖。
"""

from __future__ import annotations

import logging
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from tstdx.errors import (
    AllHostsUnreachable,
    ConnectionClosed,
    ConnectionFailed,
    FramingError,
    ReadTimeout,
    RetryAdvice,
    TdxError,
    TransportError,
    WriteTimeout,
)
from tstdx.observability import metrics
from tstdx.transport import ConnectionPool
from tstdx.transport.base import TcpConnection
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


def _make_pool(server: FakeTdxServer, **kw) -> ConnectionPool:
    defaults = dict(slots_per_host=1, timeout=3.0, heartbeat_interval=0, handshake=False)
    defaults.update(kw)
    return ConnectionPool([HostEntry("127.0.0.1", server.port)], **defaults)


# --------------------------------------------------------------------------- #
# T5：pool.close 与在飞请求竞态（循环内复查 _closed，不重建泄漏）
# --------------------------------------------------------------------------- #
def test_closed_pool_stops_inflight_rebuild():
    """close 后在飞 request 循环内复查 _closed → ConnectionClosed 且不重建。"""
    with FakeTdxServer() as server:
        server.close_on.add(ECHO_CMD)  # 每次请求都断连 → 必然进入重试循环
        pool = _make_pool(server, max_retries=3)
        outcome: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                pool.request(ECHO_CMD, b"\x01")
                outcome["exc"] = AssertionError("请求不应成功")
            except BaseException as exc:  # noqa: BLE001
                outcome["exc"] = exc

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        # 等第一次尝试落地（server 收到请求并断连）
        deadline = time.time() + 3.0
        while time.time() < deadline and server.conn_count() == 0:
            time.sleep(0.01)
        time.sleep(0.05)  # 进入退避窗口
        conns_before = server.conn_count()
        pool.close()
        t.join(timeout=10)

        assert isinstance(outcome.get("exc"), ConnectionClosed), outcome
        time.sleep(0.3)
        assert server.conn_count() == conns_before, "close 后不得重建连接（泄漏）"


# --------------------------------------------------------------------------- #
# P1：tried_hosts 生效于选序
# --------------------------------------------------------------------------- #
class _StickyError(TransportError):
    """可重试但不换主站的错误（暴露 tried_hosts 规避的确定性场景）。"""

    default_advice = RetryAdvice(retryable=True, backoff=0.0, switch_host=False)


def test_tried_hosts_preferred_away_in_retry_order(monkeypatch):
    """快主机刚失败但分数仍占优时，重试应转向未试过的主机。"""
    attempts: list[int] = []

    class StubConn:
        def __init__(self, host, port, **kw):
            self.host = host
            self.port = port
            self.connected = True
            self.spec = None
            self.stats = type("S", (), {"last_used": 0.0, "created_at": time.time()})()

        def connect(self):
            return self

        def request(self, method, body=(), **kw):
            attempts.append(self.port)
            if self.port == 1:  # 「快」主机恒失败（sticky，不换主站建议）
                raise _StickyError("sticky")
            return object()  # 慢主机成功

        def ping(self, *a, **kw):
            return 1.0

        def close(self):
            pass

    monkeypatch.setattr("tstdx.transport.pool.TcpConnection", StubConn)
    fast = HostEntry("10.0.0.1", 1, rtt_ms=1.0)  # score=1，失败后 score=4 仍居首
    slow = HostEntry("10.0.0.2", 2, rtt_ms=None)  # score=1e6
    pool = ConnectionPool([fast, slow], slots_per_host=1, heartbeat_interval=0, max_retries=2)

    result = pool.request(ECHO_CMD, b"")
    assert result is not None
    # 旧实现（slots[0]）会连续敲快主机 → attempts == [1, 1, 1] 并最终失败；
    # 规避后第二次尝试即转向未试过的慢主机。
    assert attempts == [1, 2], f"选序应规避已试主机: {attempts}"


# --------------------------------------------------------------------------- #
# P1：ping() 的 zip_size 上限（对齐 read_frame 的 max_frame_bytes）
# --------------------------------------------------------------------------- #
def test_ping_rejects_oversized_zip_size():
    with FakeTdxServer() as server:
        server.hb_zip_size = 0xFFFF  # 65535 > max_frame_bytes(32768)
        conn = TcpConnection("127.0.0.1", server.port, timeout=3.0)
        conn.connect()
        try:
            with pytest.raises(FramingError) as ei:
                conn.ping(HB_CMD)
        finally:
            conn.close()
        assert "过大" in str(ei.value)
        # 错位流不可复用：ping 失败必须弃连
        assert not conn.connected


# --------------------------------------------------------------------------- #
# T1：超时跨版本归类（ReadTimeout / WriteTimeout advice 真正可达）
# --------------------------------------------------------------------------- #
class _TimeoutSock:
    """recv/sendall 抛 socket.timeout 的假 socket（含 close 需要的方法）。"""

    def recv(self, n):  # noqa: ARG002
        raise socket.timeout("timed out")  # noqa: UP041 - 刻意用旧别名验证双分支归类

    def sendall(self, data):  # noqa: ARG002
        raise socket.timeout("timed out")  # noqa: UP041 - 同上

    def settimeout(self, v):  # noqa: ARG002
        pass

    def shutdown(self, how):  # noqa: ARG002
        pass

    def close(self):
        pass

    def fileno(self):
        return -1


def test_recv_timeout_classified_as_read_timeout():
    conn = TcpConnection("fake", 1)
    conn._sock = _TimeoutSock()
    with pytest.raises(ReadTimeout) as ei:
        conn.request(ECHO_CMD, b"x")
    advice = ei.value.advice
    assert advice.retryable and advice.backoff > 0 and advice.switch_host


def test_send_timeout_classified_as_write_timeout():
    conn = TcpConnection("fake", 1)
    conn._sock = _TimeoutSock()
    with pytest.raises(WriteTimeout):
        conn.request(ECHO_CMD, b"x")


def test_connect_timeout_classified(monkeypatch):
    def _raise(addr, timeout):  # noqa: ARG001
        raise socket.timeout("connect timed out")  # noqa: UP041 - 刻意用旧别名验证归类

    monkeypatch.setattr(socket, "create_connection", _raise)
    conn = TcpConnection("fake", 1, connect_timeout=0.1)
    with pytest.raises(ConnectionFailed) as ei:
        conn.connect()
    assert "超时" in str(ei.value)


def test_read_timeout_end_to_end_and_advice():
    """静默 server → 传输层 ReadTimeout（作为 cause），advice 齐全可被池消费。"""
    with FakeTdxServer() as server:
        server.silent.add(ECHO_CMD)
        pool = _make_pool(server, timeout=0.3, max_retries=0)
        with pytest.raises(AllHostsUnreachable) as ei:
            pool.request(ECHO_CMD, b"x")
        cause = ei.value.cause  # TdxError 的 cause 走自定义属性（非 __cause__）
        assert isinstance(cause, ReadTimeout)
        advice = cause.advice
        assert advice.retryable and advice.backoff == pytest.approx(1.0)
        assert advice.switch_host


# --------------------------------------------------------------------------- #
# request_multi（同步）：多帧合并 / 续帧中断降级弃连
# --------------------------------------------------------------------------- #
def test_request_multi_merges_continuation_frames():
    with FakeTdxServer() as server:
        pool = _make_pool(server)
        body = multi_body(count=5, record_size=4, chunk_size=6)
        merged = pool.request_multi(MULTI_CMD, body, record_size=4)
        assert merged.payload == multi_blob(5, 4)
        assert server.all_misframed() == []
        pool.close()


def test_request_multi_partial_data_degrades_and_drops():
    """续帧中断：降级返回已收到的部分数据（顺序前缀有效，与 async 版一致）、
    连接弃置（不残留半包）。"""
    with FakeTdxServer() as server:
        # 首帧正常响应，随后断连 → 续帧读取必然中断
        server.close_after.add(MULTI_CMD)
        pool = _make_pool(server)
        body = multi_body(count=5, record_size=4, chunk_size=6)
        merged = pool.request_multi(MULTI_CMD, body, record_size=4)
        # got=6 < need=20 → 保留部分数据（深审 M1：payload 是有效前缀，
        # 不再清空伪装成无数据）
        assert merged.payload == multi_blob(5, 4)[:6]
        # 对齐 _mark_failure → _drop：中断后连接必须弃置，半包不残留
        assert pool._slots[0].conn is None
        assert pool.hosts[0].failures >= 1
        pool.close()


# --------------------------------------------------------------------------- #
# G3：request 路径 metrics 埋点（不破坏 metrics 既有测试：只增不断言绝对值）
# --------------------------------------------------------------------------- #
def test_metrics_wired_on_request_paths():
    cmd_label = f"0x{ECHO_CMD:04x}"
    ok_before = metrics.request_total.value(labels={"command": cmd_label, "status": "ok"})
    err_before = metrics.request_total.value(labels={"command": cmd_label, "status": "err"})
    with FakeTdxServer() as server:
        pool = _make_pool(server)
        pool.request(ECHO_CMD, b"\x01")
        pool.close()
    ok_after = metrics.request_total.value(labels={"command": cmd_label, "status": "ok"})
    assert ok_after >= ok_before + 1
    hist_key = (cmd_label,)  # Histogram 按 labelnames 元组分序列
    assert metrics.request_duration._series[hist_key]["_count"] >= 1

    with FakeTdxServer() as server:
        server.close_on.add(ECHO_CMD)
        pool = _make_pool(server, max_retries=0)
        with pytest.raises(TdxError):
            pool.request(ECHO_CMD, b"\x01")
        pool.close()
    err_after = metrics.request_total.value(labels={"command": cmd_label, "status": "err"})
    assert err_after >= err_before + 1
    assert metrics.errors_total.value(labels={"error_type": "ConnectionClosed"}) >= 1


# --------------------------------------------------------------------------- #
# 零日志：库侧只 getLogger 不配置 handler
# --------------------------------------------------------------------------- #
def test_transport_logger_has_no_handler_and_logs_failures(caplog):
    logger = logging.getLogger("tstdx.transport")
    assert logger.handlers == [], "transport 不得自行配置 handler"
    assert logger.propagate, "应交给宿主应用的 handler 体系"
    with FakeTdxServer() as server:
        server.close_on.add(ECHO_CMD)
        pool = _make_pool(server, max_retries=0)
        with (
            caplog.at_level(logging.WARNING, logger="tstdx.transport"),
            pytest.raises(TdxError),
        ):
            pool.request(ECHO_CMD, b"\x01")
        pool.close()
    records = [r for r in caplog.records if r.name == "tstdx.transport"]
    assert records, "连接失败路径应有日志"
    assert any(r.levelno >= logging.WARNING for r in records)
