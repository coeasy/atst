"""P1-2 bestip 运行时测速热更新测试。

覆盖：
* ``ConnectionPool.update_hosts``：generation-safe 重排、复用与移除；
* probe RTT 只更新 probe layer，不覆盖 request/heartbeat live health；
* ``TdxClient.bestip()`` 使用 detached HostEntry 快照后再提交热更新；
* ``TdxClient.open(bestip=True)``：触发一轮测速；
* 异步镜像 ``AsyncTdxClient.bestip()`` 具有相同 snapshot 边界。
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

# 注意：tstdx.transport.speedtest 被包 __init__ 的同名函数遮蔽，
# `import tstdx.transport.speedtest as X` 会绑到函数而非子模块，
# 必须用 importlib.import_module 取真实模块对象再 monkeypatch。
speedtest_mod = importlib.import_module("tstdx.transport.speedtest")
hosts_mod = importlib.import_module("tstdx.transport.hosts")
from tstdx.client import AsyncTdxClient, TdxClient  # noqa: E402
from tstdx.transport.hosts import HostEntry  # noqa: E402
from tstdx.transport.pool import ConnectionPool  # noqa: E402
from tstdx.transport.speedtest import ProbeResult  # noqa: E402


def _h(host: str, port: int = 7709) -> HostEntry:
    return HostEntry(host=host, port=port)


# --------------------------------------------------------------------------- #
# ConnectionPool.update_hosts
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestUpdateHosts:
    def test_reorders_and_preserves_slot_count(self):
        a, b, c = _h("1.1.1.1"), _h("2.2.2.2"), _h("3.3.3.3")
        pool = ConnectionPool([a, b, c], slots_per_host=2, heartbeat_interval=None)
        assert [s.host.host for s in pool._slots] == [
            "1.1.1.1",
            "1.1.1.1",
            "2.2.2.2",
            "2.2.2.2",
            "3.3.3.3",
            "3.3.3.3",
        ]
        updated = pool.update_hosts([_h("3.3.3.3"), _h("1.1.1.1"), _h("2.2.2.2")])
        assert [h.host for h in updated] == ["3.3.3.3", "1.1.1.1", "2.2.2.2"]
        assert [h.host for h in pool.hosts] == ["3.3.3.3", "1.1.1.1", "2.2.2.2"]
        assert [s.host.host for s in pool._slots] == [
            "3.3.3.3",
            "3.3.3.3",
            "1.1.1.1",
            "1.1.1.1",
            "2.2.2.2",
            "2.2.2.2",
        ]
        pool.close()

    def test_drops_removed_hosts(self):
        pool = ConnectionPool(
            [_h("1.1.1.1"), _h("2.2.2.2")], slots_per_host=1, heartbeat_interval=None
        )
        updated = pool.update_hosts([_h("2.2.2.2")])
        assert [h.host for h in updated] == ["2.2.2.2"]
        assert len(pool._slots) == 1
        assert pool._slots[0].host.host == "2.2.2.2"
        pool.close()

    def test_refreshes_probe_metrics_without_overwriting_live_health(self, seed_pool_health):
        """Probe observations may refresh RTT but never import probe failure state.

        A freshly constructed pool deliberately starts a new runtime-health
        lifecycle (see ``_pool_family_hardening``): it inherits selector identity
        and probe latency only, never caller-owned request failures. Live health
        is therefore seeded *after* construction, exactly as a real request would
        accrue it on the pool-owned host.
        """
        pool = ConnectionPool([_h("1.1.1.1")], slots_per_host=1, heartbeat_interval=None)
        fresh = pool.hosts[0]
        assert fresh.failures == 0 and fresh.live_rtt_ms is None
        live = seed_pool_health(
            pool,
            failures=4,
            biz_failures=2,
            last_error="request timeout",
            live_rtt_ms=8.0,
        )
        assert live is fresh

        new_entry = _h("1.1.1.1")
        new_entry.rtt_ms = 12.5
        new_entry.failures = 2
        new_entry.last_error = "probe failure must not win"
        pool.update_hosts([new_entry])

        assert pool.hosts[0].rtt_ms == 12.5
        assert pool.hosts[0].failures == 4
        assert pool.hosts[0].biz_failures == 2
        assert pool.hosts[0].last_error == "request timeout"
        assert pool.hosts[0].live_rtt_ms == 8.0
        assert pool._slots[0].host is pool.hosts[0]
        pool.close()


# --------------------------------------------------------------------------- #
# TdxClient.bestip
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestBestip:
    def _client(self) -> TdxClient:
        hosts = [_h("1.1.1.1"), _h("2.2.2.2"), _h("3.3.3.3")]
        pool = ConnectionPool(hosts, slots_per_host=1, heartbeat_interval=None)
        return TdxClient(pool=pool)

    def test_sorts_by_rtt_and_updates_pool(self, monkeypatch):
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, connect_ms=40.0, rtt_ms=80.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=True, connect_ms=10.0, rtt_ms=15.0),
            ProbeResult(host="3.3.3.3", port=7709, ok=False, error="timeout"),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        out = client.bestip(save_ranking=False, keep_failures=True)
        assert out == results
        assert [h.host for h in client._pool.hosts] == ["2.2.2.2", "1.1.1.1", "3.3.3.3"]
        assert client._pool.hosts[0].rtt_ms == 15.0
        client._pool.close()

    def test_probe_uses_detached_host_snapshots(self, monkeypatch):
        client = self._client()
        original = client._pool.hosts[0]
        original.rtt_ms = 55.0
        original.live_rtt_ms = 7.0

        def fake_speedtest(hosts, **kwargs):
            del kwargs
            assert hosts[0] is not original
            hosts[0].rtt_ms = 1.0
            hosts[0].live_rtt_ms = 999.0
            assert original.rtt_ms == 55.0
            assert original.live_rtt_ms == 7.0
            return [ProbeResult(host=host.host, port=host.port, ok=True, rtt_ms=2.0) for host in hosts]

        monkeypatch.setattr(speedtest_mod, "speedtest", fake_speedtest)
        client.bestip(save_ranking=False)

        assert original.rtt_ms == 55.0
        assert original.live_rtt_ms == 7.0
        assert all(host is not original for host in client._pool.hosts)
        assert client._pool.hosts[0].live_rtt_ms == 7.0
        client._pool.close()

    def test_drop_failures(self, monkeypatch):
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=30.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=False, error="refused"),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        client.bestip(save_ranking=False, keep_failures=False)
        assert [h.host for h in client._pool.hosts] == ["1.1.1.1"]
        client._pool.close()

    def test_save_ranking_commits_after_pool_publication(self, monkeypatch):
        results = [ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=20.0)]
        events: list[str] = []

        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)

        class FakeStore:
            def update(self, entries):
                assert [entry.host for entry in entries] == ["1.1.1.1"]
                events.append("ranking")

        monkeypatch.setattr(hosts_mod, "RankingStore", FakeStore)
        client = TdxClient(pool=ConnectionPool([_h("1.1.1.1")], heartbeat_interval=None))
        original_update = client._pool.update_hosts

        def tracked_update(entries):
            events.append("pool")
            return original_update(entries)

        monkeypatch.setattr(client._pool, "update_hosts", tracked_update)
        client.bestip(save_ranking=True)

        assert events == ["pool", "ranking"]
        client._pool.close()

    def test_pool_commit_failure_never_persists_ranking(self, monkeypatch):
        results = [ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=20.0)]
        writes: list[list[HostEntry]] = []

        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)

        class FakeStore:
            def update(self, entries):
                writes.append(list(entries))

        monkeypatch.setattr(hosts_mod, "RankingStore", FakeStore)
        client = TdxClient(pool=ConnectionPool([_h("1.1.1.1")], heartbeat_interval=None))

        def fail_update(entries):
            del entries
            raise RuntimeError("pool commit failed")

        monkeypatch.setattr(client._pool, "update_hosts", fail_update)
        with pytest.raises(RuntimeError, match="pool commit failed"):
            client.bestip(save_ranking=True)

        assert writes == []
        client._pool.close()

    def test_open_bestip_triggers(self, monkeypatch):
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=10.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=True, rtt_ms=50.0),
            ProbeResult(host="3.3.3.3", port=7709, ok=True, rtt_ms=90.0),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        client.open(bestip=True, save_ranking=False)
        assert [h.host for h in client._pool.hosts] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
        assert client._pool.hosts[0].rtt_ms == 10.0
        client._pool.close()

    def test_empty_update_is_noop(self):
        pool = ConnectionPool([_h("1.1.1.1")], heartbeat_interval=None)
        pool.update_hosts([])
        assert len(pool.hosts) == 1
        pool.close()


# --------------------------------------------------------------------------- #
# 异步镜像
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestBestipAsync:
    def test_async_bestip_updates_pool(self, monkeypatch):
        results = [
            ProbeResult(host="2.2.2.2", port=7709, ok=True, rtt_ms=15.0),
            ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=80.0),
            ProbeResult(host="3.3.3.3", port=7709, ok=False, error="timeout"),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)

        async def _run():
            from tstdx.transport.async_ import AsyncConnectionPool

            pool = AsyncConnectionPool(
                [_h("1.1.1.1"), _h("2.2.2.2"), _h("3.3.3.3")],
                slots_per_host=1,
                heartbeat_interval=None,
            )
            client = AsyncTdxClient(pool=pool)
            out = await client.bestip(save_ranking=False, keep_failures=True)
            assert out == results
            assert [h.host for h in client._pool.hosts] == ["2.2.2.2", "1.1.1.1", "3.3.3.3"]
            await pool.close()

        asyncio.run(_run())

    def test_async_probe_uses_detached_host_snapshots(self, monkeypatch):
        async def _run():
            from tstdx.transport.async_ import AsyncConnectionPool

            pool = AsyncConnectionPool(
                [_h("1.1.1.1"), _h("2.2.2.2")],
                slots_per_host=1,
                heartbeat_interval=None,
            )
            client = AsyncTdxClient(pool=pool)
            original = pool.hosts[0]
            original.rtt_ms = 44.0
            original.live_rtt_ms = 6.0

            def fake_speedtest(hosts, **kwargs):
                del kwargs
                assert hosts[0] is not original
                hosts[0].rtt_ms = 1.0
                hosts[0].live_rtt_ms = 999.0
                assert original.rtt_ms == 44.0
                assert original.live_rtt_ms == 6.0
                return [
                    ProbeResult(host=host.host, port=host.port, ok=True, rtt_ms=3.0)
                    for host in hosts
                ]

            monkeypatch.setattr(speedtest_mod, "speedtest", fake_speedtest)
            await client.bestip(save_ranking=False)

            assert original.rtt_ms == 44.0
            assert original.live_rtt_ms == 6.0
            assert all(host is not original for host in pool.hosts)
            assert pool.hosts[0].live_rtt_ms == 6.0
            await pool.close()

        asyncio.run(_run())
