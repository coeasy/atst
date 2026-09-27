"""连接池故障转移测试（§12.3 / Q1）。

用 fake TcpConnection 注入，不依赖真实主站，覆盖：
- 首台可用即成功
- 换主站（故障转移推进到下一台候选）
- 尝试次数至少覆盖池内每台主站一次（2026-09-01 修复）
- 失败计数 / 成功清零
- R1：连接失败达阈值触发后台测速，且防抖只触发一次
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Iterable

import pytest

from atst.errors import AllHostsUnreachable, ConnectionFailed, ReadTimeout
from atst.protocol.commands import Family
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool


class _Frame:
    """最小响应帧替身（request 成功返回即可）。"""


class _FakeConn:
    """可控的 TcpConnection 替身。

    ``down_hosts`` 集合里的主机 connect() 抛 ConnectionFailed；
    ``request_fail_hosts`` 集合里的主机 connect() 成功但 request() 抛
    业务级错误（模拟连接正常、业务帧失败）。
    """

    down_hosts: set[str] = set()
    request_fail_hosts: set[str] = set()
    calls = 0

    def __init__(
        self,
        host,
        port=7709,
        *,
        timeout=3.0,
        connect_timeout=None,
        use_tls=False,
        keepalive=True,
        slot_id=0,
        family=Family.STANDARD,
        handshake=True,
        handshake_strict=False,
        spec=None,
    ):
        self.host = host
        self.port = port
        self.slot_id = slot_id
        self._ok = False
        now = time.time()
        self.stats = type("S", (), {"last_used": now, "created_at": now})()

    @property
    def connected(self):
        return self._ok

    def connect(self):
        type(self).calls += 1
        if self.host in _FakeConn.down_hosts:
            raise ConnectionFailed(f"连接 {self.host} 失败(fake)")
        self._ok = True
        return self

    def request(self, method, body=b"", *, timeout=None, compress=False):
        if self.host in _FakeConn.request_fail_hosts:
            raise ReadTimeout(f"业务帧超时(fake) {self.host}")
        return _Frame()

    def close(self):
        self._ok = False


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeConn.down_hosts = set()
    _FakeConn.request_fail_hosts = set()
    _FakeConn.calls = 0
    yield
    _FakeConn.down_hosts = set()
    _FakeConn.request_fail_hosts = set()
    _FakeConn.calls = 0


def _pool(hosts: Iterable[str], **kw) -> ConnectionPool:
    return ConnectionPool(
        [HostEntry(host=h) for h in hosts],
        slots_per_host=1,
        heartbeat_interval=None,
        **kw,
    )


@pytest.mark.unit
class TestConnectionPoolFailover:
    """连接池故障转移测试。"""

    def test_first_host_up_ok(self, monkeypatch):
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1", "2.2.2.2"])
        frame = pool.request(0x0530, b"x")
        assert isinstance(frame, _Frame)
        assert pool.stats.requests == 1

    def test_failover_reaches_second_host(self, monkeypatch):
        _FakeConn.down_hosts = {"1.1.1.1"}
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1", "2.2.2.2"], max_retries=1)
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)  # 跳过退避 sleep
            frame = pool.request(0x0530, b"x")
        assert isinstance(frame, _Frame)
        assert pool.stats.host_switches == 1

    def test_attempts_cover_every_host(self, monkeypatch):
        """即使 max_retries 很小，尝试次数也要覆盖池内每台主站一次。"""
        down = {"1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"}
        _FakeConn.down_hosts = down
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(list(down), max_retries=0)
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)
            with pytest.raises(AllHostsUnreachable) as ei:
                pool.request(0x0530, b"x")
        tried = set(ei.value.context["hosts"])
        assert tried == {f"{h}:7709" for h in down}

    def test_success_resets_host_failures(self, monkeypatch):
        _FakeConn.down_hosts = {"1.1.1.1"}
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1", "2.2.2.2"], max_retries=2)
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)
            pool.request(0x0530, b"x")
        good = next(s for s in pool._slots if s.host.host == "2.2.2.2")
        bad = next(s for s in pool._slots if s.host.host == "1.1.1.1")
        assert good.host.failures == 0
        assert bad.host.failures >= 1

    def test_all_hosts_unreachable_when_everything_down(self, monkeypatch):
        _FakeConn.down_hosts = {"1.1.1.1", "2.2.2.2"}
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1", "2.2.2.2"], max_retries=0)
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)
            with pytest.raises(AllHostsUnreachable):
                pool.request(0x0530, b"x")

    def test_speedtest_triggered_once_on_threshold(self, monkeypatch):
        """R1：连接失败达阈值触发后台测速，且防抖只触发一次。"""
        down = {"1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"}
        _FakeConn.down_hosts = down
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        calls: list[int] = []

        def _fake_speedtest(*a, **k):
            calls.append(1)
            return []

        # The hardened background trigger is probe-only: it probes via
        # ``speedtest`` and then commits observations, so patch that entry point
        # (not the bypassed ``speedtest_and_save``) and stub the ranking store so
        # the worker never writes the user's real ranking file.
        speedtest_mod = importlib.import_module("atst.transport.speedtest")
        pool_mod = importlib.import_module("atst.transport.pool")
        monkeypatch.setattr(speedtest_mod, "speedtest", _fake_speedtest)
        monkeypatch.setattr(
            pool_mod,
            "RankingStore",
            lambda *a, **k: type("_Store", (), {"update": lambda self, entries: None})(),
        )
        pool = _pool(list(down), max_retries=0, speedtest_threshold=2)
        # 第一次请求：触发后台测速；第二次：防抖不再触发
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)
            with pytest.raises(AllHostsUnreachable):
                pool.request(0x0530, b"x")
            with pytest.raises(AllHostsUnreachable):
                pool.request(0x0530, b"x")
        deadline = time.time() + 2
        while not calls and time.time() < deadline:
            time.sleep(0.01)
        assert len(calls) == 1

    def test_biz_failure_counts_and_degrades(self, monkeypatch):
        """R2：连接正常但业务帧失败 → 单独记 biz_failures 并降权。"""
        _FakeConn.request_fail_hosts = {"1.1.1.1"}
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1", "2.2.2.2"], max_retries=2)
        with monkeypatch.context() as m:
            m.setattr("time.sleep", lambda *a: None)
            frame = pool.request(0x0530, b"x")  # 1.1.1.1 业务失败 → 换 2.2.2.2 成功
        assert isinstance(frame, _Frame)
        bad = next(s for s in pool._slots if s.host.host == "1.1.1.1")
        assert bad.host.biz_failures >= 1
        assert bad.host.failures >= 1
        good = next(s for s in pool._slots if s.host.host == "2.2.2.2")
        assert good.host.biz_failures == 0
        # 业务失败主机的 score 被抬升（R2 降权）
        assert bad.host.score > good.host.score

    def test_idle_sweep_reclaims_unused_connections(self, monkeypatch):
        """M6：闲置连接被回收（idle_timeout 到期后 slot.conn 置空）。"""
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1"], idle_timeout=0.1)
        # 建连
        frame = pool.request(0x0530, b"x")
        assert isinstance(frame, _Frame)
        slot = pool._slots[0]
        assert slot.conn is not None
        # 把 last_used 拨到很久以前 → 回收
        slot.conn.stats.last_used = time.time() - 100
        pool._sweep_idle()
        assert slot.conn is None

    def test_idle_sweep_respects_active_connection(self, monkeypatch):
        """M6：最近使用的连接不被回收。"""
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1"], idle_timeout=60)
        frame = pool.request(0x0530, b"x")
        assert isinstance(frame, _Frame)
        pool._sweep_idle()
        assert pool._slots[0].conn is not None

    def test_idle_sweep_disabled_when_zero(self, monkeypatch):
        """M6：idle_timeout<=0 时不做回收。"""
        monkeypatch.setattr("atst.transport.pool.TcpConnection", _FakeConn)
        pool = _pool(["1.1.1.1"], idle_timeout=0)
        frame = pool.request(0x0530, b"x")
        assert isinstance(frame, _Frame)
        pool._slots[0].conn.stats.last_used = time.time() - 9999
        pool._sweep_idle()
        assert pool._slots[0].conn is not None
