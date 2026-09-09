from __future__ import annotations

import json
from pathlib import Path

from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry, RankingStore, resolve_hosts


def test_persisted_ranking_excludes_process_local_live_health(tmp_path: Path) -> None:
    store = RankingStore(tmp_path / "ranking.json")
    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                rtt_ms=3.0,
                live_rtt_ms=12.0,
                live_ok_at=99.0,
                circuit_probe_inflight=True,
                last_ok=1.0,
            )
        ]
    )

    raw = json.loads((tmp_path / "ranking.json").read_text(encoding="utf-8"))
    persisted = raw["entries"]["1.2.3.4:7709"]
    assert "live_rtt_ms" not in persisted
    assert "live_ok_at" not in persisted
    assert "circuit_probe_inflight" not in persisted

    loaded = store.load()["1.2.3.4:7709"]
    assert loaded.rtt_ms == 3.0
    assert loaded.live_rtt_ms is None
    assert loaded.live_ok_at is None
    assert loaded.circuit_probe_inflight is False


def test_ranking_file_cannot_inject_process_local_live_health(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {
                    "1.2.3.4:7709": {
                        "host": "1.2.3.4",
                        "port": 7709,
                        "family": Family.STANDARD,
                        "rtt_ms": 3.0,
                        "live_rtt_ms": 0.1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}


def test_ranking_key_mismatch_invalidates_cache(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {
                    "9.9.9.9:7709": {
                        "host": "1.2.3.4",
                        "port": 7709,
                        "family": Family.STANDARD,
                        "rtt_ms": 3.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}


def test_explicit_live_health_wins_over_persisted_probe_rtt(tmp_path: Path) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                rtt_ms=3.0,
                last_ok=1.0,
            )
        ]
    )
    explicit = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        live_rtt_ms=12.0,
        live_ok_at=2.0,
    )

    resolved = resolve_hosts(
        [explicit],
        family=Family.STANDARD,
        ranking_file=ranking_file,
    )

    assert len(resolved) == 1
    assert resolved[0].rtt_ms == 3.0
    assert resolved[0].live_rtt_ms == 12.0
    assert resolved[0].live_ok_at == 2.0
    assert resolved[0].score == 12.0
