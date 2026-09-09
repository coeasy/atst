"""ConnectionPool 主站生命周期交错回归。

这些用例用可控阻塞把后台测速、真实请求、half-open 和 bestip 热更新
排在同一条时间线上，避免只靠高并发概率撞出竞态。
"""

from __future__ import annotations

import importlib
import threading
import time

from tstdx.errors import ConnectionFailed
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import CIRCUIT_COOLDOWN_SECONDS, ConnectionPool


class _Frame:
    pass


class _BlockingConn:
    gate = threading.Event()
    started = threading.Event()

    def __init__(self, host, port, **_kw):
        self.host = host
        self.port = port
        self.connected = True
        self.spec = None
        self._lock = threading.RLock()
        self.stats = type("Stats", (), {"last_used": 0.0, "created_at": time.time()})()
        self.closed = False

    def connect(self):
        return self

    def request(self, *_args, **_kwargs):
        type(self).started.set()
        type(self).gate.wait(timeout=5)
        return _Frame()

    def close(self):
        self.closed = True
        self.connected = False


def test_update_hosts_retires_active_generation_without_closing_its_lease(monkeypatch):
    """热更新不得切断活动请求，且旧请求完成不得改写新 generation。"""
    _BlockingConn.gate.clear()
    _BlockingConn.started.clear()
    monkeypatch.setattr("tstdx.transport.pool.TcpConnection", _BlockingConn)
    old = HostEntry("127.0.0.1", 7709, rtt_ms=100.0)
    pool = ConnectionPool([old], slots_per_host=1, heartbeat_interval=None, max_retries=0)
    outcome: dict[str, object] = {}

    def run():
        try:
            outcome["frame"] = pool.request(0x0530, b"x")
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    assert _BlockingConn.started.wait(timeout=2)

    fresh = HostEntry("127.0.0.1", 7709, rtt_ms=5.0)
    pool.update_hosts([fresh])
    assert pool._slots[0].host is fresh
    assert pool._slots[0].conn is None

    _BlockingConn.gate.set()
    worker.join(timeout=3)
    assert "error" not in outcome
    assert fresh.rtt_ms == 5.0
    assert fresh.live_rtt_ms is None, "旧 generation 的成功不得污染新 live health"
    pool.close()


def test_half_open_allows_exactly_one_probe_under_concurrency():
    host = HostEntry("127.0.0.1", 7709)
    pool = ConnectionPool([host], slots_per_host=1, heartbeat_interval=None)
    host.circuit = "open"
    host.circuit_opened_at = time.time() - CIRCUIT_COOLDOWN_SECONDS - 1
    barrier = threading.Barrier(12)
    allowed: list[bool] = []

    def probe():
        barrier.wait(timeout=2)
        allowed.append(pool._circuit_allows(host))

    threads = [threading.Thread(target=probe) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sum(allowed) == 1
    assert host.circuit == "half_open"
    pool.close()


def test_background_speedtest_cannot_overwrite_live_health(monkeypatch):
    host = HostEntry("127.0.0.1", 7709, rtt_ms=80.0, live_rtt_ms=12.0)
    pool = ConnectionPool([host], slots_per_host=1, heartbeat_interval=None, speedtest_threshold=1)
    entered = threading.Event()
    release = threading.Event()

    def fake_speedtest(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=3)

    speedtest_mod = importlib.import_module("tstdx.transport.speedtest")
    monkeypatch.setattr(speedtest_mod, "speedtest_and_save", fake_speedtest)
    slot = pool._slots[0]
    pool._mark_failure(slot, ConnectionFailed("trigger"))
    assert entered.wait(timeout=2)

    new = HostEntry("127.0.0.1", 7709, rtt_ms=2.0)
    pool.update_hosts([new])
    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and not pool._speedtest_triggered:
        time.sleep(0.01)

    assert new.live_rtt_ms == 12.0
    assert new.rtt_ms == 2.0
    assert new.score == 12.0 * 4, "真实健康排序应优先于测速排序，但不改写测速值"
    pool.close()
