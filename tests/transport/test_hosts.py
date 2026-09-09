"""主站候选池 / 排名解析测试（§12 / Q1 / U3）。

覆盖：resolve_hosts 排序与合并、max_hosts 截断、servers 覆盖内置池、
RankingStore 持久化往返、排名文件优先生效、实测可达主站前置（U1）、
TSTDX_HOSTS 环境变量注入（U3）。
"""

from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import (
    DEFAULT_HOST_POOL,
    HostEntry,
    RankingStore,
    resolve_hosts,
)


@pytest.mark.unit
class TestResolveHosts:
    """主站解析与排名测试。"""

    def test_default_pool_has_verified_reachable_hosts_first(self):
        """U1：内置池前排为实测可达主站（verified=True）。"""
        verified = [e for e in DEFAULT_HOST_POOL if e.family == Family.STANDARD and e.verified]
        assert len(verified) >= 5, "实测可达主站应前置且标记 verified"
        # 第一台就是可达主站，避免冷启动首请求连吃超时
        assert DEFAULT_HOST_POOL[0].verified

    def test_resolve_default_respects_max_hosts(self):
        hosts = resolve_hosts(None)
        assert len(hosts) <= 8
        assert len(hosts) >= 1
        assert all(e.family == Family.STANDARD for e in hosts)

    @pytest.mark.parametrize("value", [0, -1, 65, True, 1.5])
    def test_resolve_max_hosts_rejects_invalid_limits(self, value):
        with pytest.raises(ConfigError, match="max_hosts"):
            resolve_hosts(None, max_hosts=value)

    def test_resolve_servers_override_pool(self):
        hosts = resolve_hosts(["1.2.3.4:7709", "5.6.7.8:7709"], max_hosts=1)
        assert len(hosts) == 1
        assert hosts[0].host == "1.2.3.4"
        assert hosts[0].port == 7709

    def test_resolve_servers_keep_order(self):
        hosts = resolve_hosts(["5.6.7.8:7709", "1.2.3.4:7709"], max_hosts=2)
        assert [e.host for e in hosts] == ["5.6.7.8", "1.2.3.4"]

    def test_ranking_store_roundtrip(self, tmp_path):
        store = RankingStore(str(tmp_path / "ranking.json"))
        entries = [HostEntry(host="1.2.3.4", port=7709, rtt_ms=5.0, connect_ms=2.0, failures=0)]
        store.update(entries)
        loaded = store.load()
        assert loaded["1.2.3.4:7709"].rtt_ms == 5.0
        assert loaded["1.2.3.4:7709"].connect_ms == 2.0

    def test_ranking_missing_file_returns_empty(self, tmp_path):
        store = RankingStore(str(tmp_path / "nope.json"))
        assert store.load() == {}

    def test_ranking_file_has_priority_in_resolve(self, tmp_path):
        """排名文件里的条目在 resolve 时优先生效（排在首位）。"""
        ranking_file = str(tmp_path / "ranking.json")
        RankingStore(ranking_file).update(
            [HostEntry(host="9.9.9.9", port=7709, rtt_ms=1.0, failures=0)]
        )
        hosts = resolve_hosts(None, ranking_file=ranking_file, use_ranking=True, max_hosts=8)
        assert hosts[0].key == "9.9.9.9:7709"

    def test_ranking_disabled_ignores_file(self, tmp_path):
        ranking_file = str(tmp_path / "ranking.json")
        RankingStore(ranking_file).update(
            [HostEntry(host="9.9.9.9", port=7709, rtt_ms=1.0, failures=0)]
        )
        hosts = resolve_hosts(None, ranking_file=ranking_file, use_ranking=False, max_hosts=8)
        assert hosts[0].key != "9.9.9.9:7709"

    # -- U3：TSTDX_HOSTS 环境变量注入 -------------------------------------- #
    def test_env_hosts_injected(self, monkeypatch):
        """TSTDX_HOSTS 设置时优先于内置候选池。"""
        monkeypatch.setenv("TSTDX_HOSTS", "180.153.18.170:7709,60.191.117.167")
        hosts = resolve_hosts(None, max_hosts=8)
        assert hosts[0].key == "180.153.18.170:7709"
        assert hosts[1].key == "60.191.117.167:7709"
        assert len(hosts) == 2

    def test_env_hosts_unset_falls_back_to_pool(self, monkeypatch):
        monkeypatch.delenv("TSTDX_HOSTS", raising=False)
        hosts = resolve_hosts(None, max_hosts=8)
        assert len(hosts) >= 1
        assert all(e.family == Family.STANDARD for e in hosts)

    def test_env_hosts_rejects_garbage_instead_of_falling_back(self, monkeypatch):
        monkeypatch.setenv("TSTDX_HOSTS", "not a host:port,,1.2.3.4:99999")

        with pytest.raises(ConfigError, match="TSTDX_HOSTS 条目无效"):
            resolve_hosts(None, max_hosts=8)

    def test_servers_still_beat_env(self, monkeypatch):
        """显式 servers 参数仍优先于环境变量。"""
        monkeypatch.setenv("TSTDX_HOSTS", "9.9.9.9:7709")
        hosts = resolve_hosts(["1.2.3.4:7709"], max_hosts=8)
        assert hosts[0].key == "1.2.3.4:7709"
