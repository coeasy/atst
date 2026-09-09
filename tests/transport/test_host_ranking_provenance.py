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


def test_same_family_ranking_only_overlays_runtime_observations(tmp_path: Path) -> None:
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
            )
        ]
    )
    explicit = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="explicit-name",
        verified=False,
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
    assert resolved[0].failures == 2
    assert resolved[0].last_error == "ranked failure"


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


def test_standard_merge_keeps_all_runtime_observation_fields(tmp_path: Path) -> None:
    store = RankingStore(str(tmp_path / "ranking.json"))
    store.update([HostEntry(host="1.2.3.4", family=Family.STANDARD, last_ok=1.0)])

    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                family=Family.STANDARD,
                rtt_ms=4.0,
                connect_ms=2.0,
                biz_failures=3,
                circuit="open",
                consec_weighted=4.5,
                circuit_opened_at=10.0,
                failures=2,
                last_error="failure",
            )
        ]
    )

    entry = store.load()["1.2.3.4:7709"]
    assert entry.rtt_ms == 4.0
    assert entry.connect_ms == 2.0
    assert entry.biz_failures == 3
    assert entry.circuit == "open"
    assert entry.consec_weighted == 4.5
    assert entry.circuit_opened_at == 10.0
    assert entry.failures == 2
    assert entry.last_error == "failure"


def test_explicit_host_entry_with_wrong_family_fails_closed() -> None:
    with pytest.raises(ConfigError, match="family 不匹配"):
        resolve_hosts(
            [HostEntry(host="1.2.3.4", family=Family.F10)],
            family=Family.STANDARD,
        )
