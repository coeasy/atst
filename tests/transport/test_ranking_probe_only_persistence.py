from __future__ import annotations

import json
from pathlib import Path

from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry, RankingStore


def test_failed_probe_removes_stale_success_without_persisting_failure_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ranking.json"
    store = RankingStore(path)
    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                connect_ms=1.0,
                rtt_ms=2.0,
            )
        ]
    )
    assert "1.2.3.4:7709" in store.load()

    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                failures=1,
                last_error="probe failed",
            )
        ]
    )

    assert store.load() == {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["entries"] == {}


def test_unmeasured_entry_is_not_persisted_as_a_ranked_candidate(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    store = RankingStore(path)

    store.save(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                connect_ms=1.0,
                rtt_ms=None,
            )
        ]
    )

    assert store.load() == {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["entries"] == {}
