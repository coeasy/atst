# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""排名文件 V2 的观测时间戳与"保鲜"读数。

为什么需要这组判据（本轮实测依据）：``hosts discover`` 把发现的主机写进排名之后，这些
条目只有"被采纳那一刻"的 RTT——``hosts scan`` 当时用 ``use_ranking=False`` 扫不到它们，
排名里也没有任何时间字段，于是掉线的主机会一直带着 12ms 的旧证据排在首位，成为冷启动
超时的来源。V2 用 ``observed_at`` 补上"这条证据什么时候测的"，并保证：

* **V1 文件不会被静默清空**——直接拒读旧版本等于升级一次丢一次用户排名；
* **失败观测会删条目**——刷新与淘汰是同一次 scan 的两面；
* **读数不参与排序**——``freshness()`` 只是给人看的视图，不改 ``score``。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from atst.transport.hosts import Family, HostEntry, RankingStore


def _write_v1(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "2026-01-01T00:00:00",
                "entries": {
                    "1.2.3.4:7709": {
                        "host": "1.2.3.4",
                        "port": 7709,
                        "family": Family.STANDARD,
                        "connect_ms": 2.0,
                        "rtt_ms": 4.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.unit
class TestRankingV2:
    def test_v1_file_still_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        _write_v1(path)

        entry = RankingStore(path).load()["1.2.3.4:7709"]
        assert entry.rtt_ms == 4.0
        assert entry.observed_at is None, "V1 没有观测时间，载入时必须是'未知'而不是伪造一个 now"

    def test_next_write_upgrades_v1_to_v2(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        _write_v1(path)

        RankingStore(path).update([HostEntry(host="1.2.3.4", rtt_ms=3.0)])

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["version"] == 2
        persisted = raw["entries"]["1.2.3.4:7709"]
        assert isinstance(persisted["observed_at"], float)
        assert persisted["rtt_ms"] == 3.0

    def test_update_stamps_observation_time(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        before = time.time()
        RankingStore(path).update([HostEntry(host="1.2.3.4", rtt_ms=4.0)])

        entry = RankingStore(path).load()["1.2.3.4:7709"]
        assert entry.observed_at is not None
        assert before - 1 <= entry.observed_at <= time.time() + 1

    def test_explicit_observation_time_is_not_overwritten(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        RankingStore(path).update(
            [HostEntry(host="1.2.3.4", rtt_ms=4.0, observed_at=1_700_000_000.0)]
        )

        assert RankingStore(path).load()["1.2.3.4:7709"].observed_at == 1_700_000_000.0

    def test_failed_observation_removes_the_entry(self, tmp_path: Path) -> None:
        """刷新与淘汰是同一次 scan 的两面：失败观测必须把旧证据清掉。"""

        path = tmp_path / "ranking.json"
        store = RankingStore(path)
        store.update([HostEntry(host="1.2.3.4", rtt_ms=4.0)])
        assert "1.2.3.4:7709" in store.load()

        store.update([HostEntry(host="1.2.3.4", failures=1, last_error="timeout")])
        assert "1.2.3.4:7709" not in store.load()


@pytest.mark.unit
class TestFreshness:
    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        freshness = RankingStore(tmp_path / "absent.json").freshness()
        assert freshness.entries == 0
        assert freshness.age_seconds() is None

    def test_reports_oldest_and_newest_observation(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        store = RankingStore(path)
        store.update(
            [
                HostEntry(host="1.1.1.1", rtt_ms=1.0, observed_at=1_000.0),
                HostEntry(host="2.2.2.2", rtt_ms=2.0, observed_at=5_000.0),
            ]
        )

        freshness = store.freshness()
        assert freshness.entries == 2
        assert freshness.oldest_observed == 1_000.0
        assert freshness.newest_observed == 5_000.0
        assert freshness.age_seconds(now=1_060.0) == 60.0

    def test_v1_file_has_no_age(self, tmp_path: Path) -> None:
        path = tmp_path / "ranking.json"
        _write_v1(path)

        freshness = RankingStore(path).freshness()
        assert freshness.entries == 1
        assert freshness.age_seconds() is None


@pytest.mark.unit
class TestRankedObservationMerge:
    """``resolve_hosts`` 叠加排名观测时，probe 元数据必须整组搬运。

    实测依据：``_apply_ranked_observation`` 早先只搬 ``connect_ms`` / ``rtt_ms``，漏了
    ``observed_at``。后果是**既在内置池又在排名文件里**的主机显示"有 rtt 但观测年龄 -"，
    按年龄判断过期的调用方会把刚测过的池内主机当成从未观测——V2 的保鲜语义在合并点
    被悄悄抹掉，而落盘、读数、展示三处各自都是对的，很难从任何单点发现。
    """

    def test_pool_host_keeps_observation_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from atst.transport.hosts import POOL_BY_FAMILY, resolve_hosts

        monkeypatch.delenv("ATST_HOSTS", raising=False)
        pool_host = POOL_BY_FAMILY[Family.STANDARD][0]
        path = tmp_path / "ranking.json"
        RankingStore(path).update(
            [
                HostEntry(
                    host=pool_host.host,
                    port=pool_host.port,
                    rtt_ms=5.0,
                    observed_at=1_700_000_000.0,
                )
            ]
        )

        resolved = resolve_hosts(None, family=Family.STANDARD, ranking_file=str(path), max_hosts=64)
        merged = next(entry for entry in resolved if entry.key == pool_host.key)
        assert merged.rtt_ms == 5.0
        assert merged.observed_at == 1_700_000_000.0, "合并点丢了 observed_at，保鲜读数会变成'-'"

    def test_host_absent_from_ranking_has_no_observation_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """没被观测过的主机必须是 ``None``——伪造一个 now 会让过期判断永远为"新鲜"。"""

        from atst.transport.hosts import POOL_BY_FAMILY, resolve_hosts

        monkeypatch.delenv("ATST_HOSTS", raising=False)
        path = tmp_path / "ranking.json"
        RankingStore(path).update([HostEntry(host="192.0.2.99", rtt_ms=5.0)])

        resolved = resolve_hosts(None, family=Family.STANDARD, ranking_file=str(path), max_hosts=64)
        untouched = next(
            entry for entry in resolved if entry.key == POOL_BY_FAMILY[Family.STANDARD][0].key
        )
        assert untouched.observed_at is None
