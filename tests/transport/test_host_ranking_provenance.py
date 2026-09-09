from __future__ import annotations

import json
from pathlib import Path

import pytest

import tstdx.transport.hosts as hosts_module
from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry, POOL_BY_FAMILY, RankingStore, resolve_hosts


def test_shared_endpoint_pools_rebind_family_and_reset_verification_provenance() -> None:
    assert POOL_BY_FAMILY[Family.F10]
    assert POOL_BY_FAMILY[Family.GOODS]
    assert all(entry.family == Family.F10 for entry in POOL_BY_FAMILY[Family.F10])
    assert all(entry.family == Family.GOODS for entry in POOL_BY_FAMILY[Family.GOODS])
    assert all(entry.verified is False for entry in POOL_BY_FAMILY[Family.F10])
    assert all(entry.verified is False for entry in POOL_BY_FAMILY[Family.GOODS])


def test_explicit_servers_cannot_be_expanded_by_requested_ranking_file(tmp_path: Path) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="9.9.9.9",
                port=7709,
                family=Family.STANDARD,
                rtt_ms=1.0,
                last_ok=1.0,
            )
        ]
    )

    resolved = resolve_hosts(
        ["1.2.3.4:7709"],
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=8,
    )

    assert [entry.key for entry in resolved] == ["1.2.3.4:7709"]


def test_environment_hosts_cannot_be_expanded_by_requested_ranking_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="9.9.9.9",
                port=7709,
                family=Family.STANDARD,
                rtt_ms=1.0,
                last_ok=1.0,
            )
        ]
    )
    monkeypatch.setenv("TSTDX_HOSTS", "1.2.3.4:7709")

    resolved = resolve_hosts(
        None,
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=8,
    )

    assert [entry.key for entry in resolved] == ["1.2.3.4:7709"]


def test_same_family_ranking_only_overlays_probe_observations(tmp_path: Path) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="1.2.3.4",
                port=7709,
                family=Family.STANDARD,
                name="ranking-name",
                verified=True,
                rtt_ms=3.0,
                failures=2,
                last_error="ranked failure",
                circuit="open",
                consec_weighted=8.0,
                circuit_opened_at=99.0,
            )
        ]
    )
    explicit = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="explicit-name",
        verified=False,
        live_rtt_ms=12.0,
        failures=1,
        last_error="current failure",
        circuit="degraded",
        consec_weighted=3.0,
    )

    resolved = resolve_hosts(
        [explicit],
        family=Family.STANDARD,
        ranking_file=ranking_file,
    )

    assert len(resolved) == 1
    assert resolved[0].name == "explicit-name"
    assert resolved[0].verified is False
    assert resolved[0].rtt_ms == 3.0
    assert resolved[0].live_rtt_ms == 12.0
    assert resolved[0].failures == 1
    assert resolved[0].last_error == "current failure"
    assert resolved[0].circuit == "degraded"
    assert resolved[0].consec_weighted == 3.0


def test_legacy_other_family_row_is_ignored_by_standard_resolver(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "1.2.3.4:7709": HostEntry(
                        host="1.2.3.4",
                        port=7709,
                        family=Family.F10,
                        rtt_ms=1.0,
                    ).to_dict()
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_hosts(
        ["1.2.3.4:7709"],
        family=Family.STANDARD,
        ranking_file=str(path),
    )

    assert len(resolved) == 1
    assert resolved[0].family == Family.STANDARD
    assert resolved[0].rtt_ms is None


def test_builtin_pool_may_retain_same_family_ranked_extra(tmp_path: Path) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="9.9.9.9",
                port=7709,
                family=Family.STANDARD,
                rtt_ms=1.0,
                last_ok=1.0,
            )
        ]
    )

    resolved = resolve_hosts(
        None,
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=32,
    )

    assert any(entry.key == "9.9.9.9:7709" for entry in resolved)
    assert all(entry.family == Family.STANDARD for entry in resolved)


def test_default_standard_resolution_consumes_persistent_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranked = HostEntry(
        host="9.9.9.9",
        port=7709,
        family=Family.STANDARD,
        rtt_ms=1.0,
        last_ok=1.0,
    )

    class FakeStore:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def load(self) -> dict[str, HostEntry]:
            return {ranked.key: ranked}

    monkeypatch.setattr(hosts_module, "RankingStore", FakeStore)

    resolved = resolve_hosts(None, family=Family.STANDARD, max_hosts=32)

    assert resolved[0].key == ranked.key


def test_default_persistent_ranking_does_not_touch_explicit_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingStore:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("explicit selector must not read default ranking")

    monkeypatch.setattr(hosts_module, "RankingStore", ExplodingStore)

    resolved = resolve_hosts(["1.2.3.4:7709"], family=Family.STANDARD)

    assert [entry.key for entry in resolved] == ["1.2.3.4:7709"]


def test_ranking_store_v1_rejects_nonstandard_family(tmp_path: Path) -> None:
    store = RankingStore(str(tmp_path / "ranking.json"))

    with pytest.raises(ConfigError, match="仅支持 STANDARD"):
        store.update([HostEntry(host="1.2.3.4", port=7709, family=Family.F10)])


def test_standard_update_heals_legacy_nonstandard_row(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": {
                    "1.2.3.4:7709": HostEntry(
                        host="1.2.3.4",
                        port=7709,
                        family=Family.F10,
                        rtt_ms=1.0,
                    ).to_dict(),
                    "2.2.2.2:7709": HostEntry(
                        host="2.2.2.2",
                        port=7709,
                        family=Family.F10,
                        rtt_ms=2.0,
                    ).to_dict(),
                },
            }
        ),
        encoding="utf-8",
    )
    store = RankingStore(str(path))

    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                port=7709,
                family=Family.STANDARD,
                rtt_ms=9.0,
                last_ok=2.0,
            )
        ]
    )

    loaded = store.load()
    assert list(loaded) == ["1.2.3.4:7709"]
    assert loaded["1.2.3.4:7709"].family == Family.STANDARD
    assert loaded["1.2.3.4:7709"].rtt_ms == 9.0


def test_standard_merge_persists_probe_only_and_resets_runtime_state(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    store = RankingStore(str(path))
    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                name="runtime-name",
                verified=True,
                rtt_ms=4.0,
                connect_ms=2.0,
                live_rtt_ms=1.0,
                live_ok_at=20.0,
                biz_failures=3,
                circuit="open",
                consec_weighted=4.5,
                circuit_opened_at=10.0,
                failures=2,
                last_ok=9.0,
                last_error="failure",
                circuit_probe_inflight=True,
            )
        ]
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    persisted = raw["entries"]["1.2.3.4:7709"]
    assert set(persisted) == {"host", "port", "family", "connect_ms", "rtt_ms"}

    entry = store.load()["1.2.3.4:7709"]
    assert entry.rtt_ms == 4.0
    assert entry.connect_ms == 2.0
    assert entry.name == ""
    assert entry.verified is False
    assert entry.live_rtt_ms is None
    assert entry.live_ok_at is None
    assert entry.biz_failures == 0
    assert entry.circuit == "healthy"
    assert entry.consec_weighted == 0.0
    assert entry.circuit_opened_at == 0.0
    assert entry.failures == 0
    assert entry.last_ok is None
    assert entry.last_error == ""
    assert entry.circuit_probe_inflight is False


def test_legacy_v1_runtime_health_is_sanitized_without_losing_probe_latency(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ranking.json"
    legacy = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        name="legacy-name",
        verified=True,
        connect_ms=2.0,
        rtt_ms=4.0,
        failures=5,
        biz_failures=2,
        last_ok=3.0,
        last_error="legacy failure",
        circuit="open",
        consec_weighted=9.0,
        circuit_opened_at=8.0,
    ).to_dict()
    path.write_text(
        json.dumps(
            {
                "version": RankingStore.VERSION,
                "entries": {"1.2.3.4:7709": legacy},
            }
        ),
        encoding="utf-8",
    )

    loaded = RankingStore(path).load()["1.2.3.4:7709"]
    assert loaded.connect_ms == 2.0
    assert loaded.rtt_ms == 4.0
    assert loaded.failures == 0
    assert loaded.biz_failures == 0
    assert loaded.last_ok is None
    assert loaded.last_error == ""
    assert loaded.circuit == "healthy"
    assert loaded.consec_weighted == 0.0
    assert loaded.circuit_opened_at == 0.0
    assert loaded.name == ""
    assert loaded.verified is False

    RankingStore(path).update([loaded])
    rewritten = json.loads(path.read_text(encoding="utf-8"))["entries"]["1.2.3.4:7709"]
    assert set(rewritten) == {"host", "port", "family", "connect_ms", "rtt_ms"}


def test_explicit_host_entry_with_wrong_family_fails_closed() -> None:
    with pytest.raises(ConfigError, match="family 不匹配"):
        resolve_hosts(
            [HostEntry(host="1.2.3.4", family=Family.F10)],
            family=Family.STANDARD,
        )
