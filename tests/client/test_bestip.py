"""P1-2 bestip 运行时测速热更新测试。

覆盖：
* ``ConnectionPool.update_hosts``：按新序重建槽位、复用连接、丢弃移除主站；
* ``TdxClient.bestip()``：测速→排序→热更新（monkeypatch 探测，不真连网）；
* ``TdxClient.open(bestip=True)``：触发一轮测速；
* 异步镜像 ``AsyncTdxClient.bestip()``。
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

# 注意：tstdx.transport.speedtest 被包 __init__ 的同名函数遮蔽，
# `import tstdx.transport.speedtest as X` 会绑到函数而非子模块，
# 必须用 importlib.import_module 取真实模块对象再 monkeypatch。
speedtest_mod = importlib.import_module("tstdx.transport.speedtest")
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
        # 新序：c 最快在前
        updated = pool.update_hosts([_h("3.3.3.3"), _h("1.1.1.1"), _h("2.2.2.2")])
        assert [h.host for h in updated] == ["3.3.3.3", "1.1.1.1", "2.2.2.2"]
        assert [h.host for h in pool.hosts] == ["3.3.3.3", "1.1.1.1", "2.2.2.2"]
        # 槽位数与顺序同步
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

    def test_refreshes_metrics_on_existing_host(self):
        """复用槽位时刷新 HostEntry（带入新 RTT / 失败计数）。"""
        pool = ConnectionPool([_h("1.1.1.1")], slots_per_host=1, heartbeat_interval=None)
        new_entry = _h("1.1.1.1")
        new_entry.rtt_ms = 12.5
        new_entry.failures = 2
        pool.update_hosts([new_entry])
        assert pool.hosts[0].rtt_ms == 12.5
        assert pool.hosts[0].failures == 2
        assert pool._slots[0].host.rtt_ms == 12.5
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
        """按 RTT 升序热更新主站池。"""
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, connect_ms=40.0, rtt_ms=80.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=True, connect_ms=10.0, rtt_ms=15.0),
            ProbeResult(host="3.3.3.3", port=7709, ok=False, error="timeout"),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        out = client.bestip(save_ranking=False, keep_failures=True)
        assert out == results
        # 热更新：RTT 升序（失败的排最后）
        assert [h.host for h in client._pool.hosts] == ["2.2.2.2", "1.1.1.1", "3.3.3.3"]
        assert client._pool.hosts[0].rtt_ms == 15.0
        client.close()

    def test_drop_failures(self, monkeypatch):
        """keep_failures=False 时失败主站从池中移除。"""
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=30.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=False, error="refused"),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        client.bestip(save_ranking=False, keep_failures=False)
        assert [h.host for h in client._pool.hosts] == ["1.1.1.1"]
        client.close()

    def test_save_ranking_writes_file(self, monkeypatch, tmp_path):
        """save_ranking=True 时调用 speedtest_and_save 并写排名文件。"""
        results = [ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=20.0)]
        calls: list = []

        def _fake_and_save(*a, **k):
            calls.append((a, k))
            return results

        monkeypatch.setattr(speedtest_mod, "speedtest_and_save", _fake_and_save)
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = TdxClient(pool=ConnectionPool([_h("1.1.1.1")], heartbeat_interval=None))
        client.bestip(save_ranking=True)
        assert calls, "save_ranking=True 应走 speedtest_and_save"
        client.close()

    def test_open_bestip_triggers(self, monkeypatch):
        results = [
            ProbeResult(host="1.1.1.1", port=7709, ok=True, rtt_ms=10.0),
            ProbeResult(host="2.2.2.2", port=7709, ok=True, rtt_ms=50.0),
            ProbeResult(host="3.3.3.3", port=7709, ok=True, rtt_ms=90.0),
        ]
        monkeypatch.setattr(speedtest_mod, "speedtest", lambda *a, **k: results)
        client = self._client()
        client.open(bestip=True, save_ranking=False)
        # 已触发测速并热更新：最快主站前置
        assert [h.host for h in client._pool.hosts] == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
        assert client._pool.hosts[0].rtt_ms == 10.0
        client.close()

    def test_empty_pool_returns_empty(self):
        pool = ConnectionPool([_h("1.1.1.1")], heartbeat_interval=None)
        pool.update_hosts([])  # 空 → 保持原池
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
            await client.close()

        asyncio.run(_run())
