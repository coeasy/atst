from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry, POOL_BY_FAMILY, RankingStore, resolve_hosts


def test_shared_endpoint_pools_rebind_canonical_family_identity() -> None:
    assert POOL_BY_FAMILY[Family.F10]
    assert POOL_BY_FAMILY[Family.GOODS]
    assert all(entry.family == Family.F10 for entry in POOL_BY_FAMILY[Family.F10])
    assert all(entry.family == Family.GOODS for entry in POOL_BY_FAMILY[Family.GOODS])


def test_explicit_servers_cannot_be_expanded_by_ranking_file(tmp_path) -> None:
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

    hosts = resolve_hosts(
        ["1.2.3.4:7709"],
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=8,
    )

    assert [entry.key for entry in hosts] == ["1.2.3.4:7709"]


def test_environment_hosts_cannot_be_expanded_by_ranking_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
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

    hosts = resolve_hosts(
        None,
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=8,
    )

    assert [entry.key for entry in hosts] == ["1.2.3.4:7709"]


def test_other_family_ranking_cannot_replace_matching_explicit_endpoint(tmp_path) -> None:
    ranking_file = str(tmp_path / "ranking.json")
    RankingStore(ranking_file).update(
        [
            HostEntry(
                host="1.2.3.4",
                port=7709,
                family=Family.F10,
                rtt_ms=1.0,
                last_ok=1.0,
            )
        ]
    )

    hosts = resolve_hosts(
        ["1.2.3.4:7709"],
        family=Family.STANDARD,
        ranking_file=ranking_file,
    )

    assert len(hosts) == 1
    assert hosts[0].family == Family.STANDARD
    assert hosts[0].rtt_ms is None


def test_builtin_pool_may_retain_same_family_ranked_extra(tmp_path) -> None:
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

    hosts = resolve_hosts(
        None,
        family=Family.STANDARD,
        ranking_file=ranking_file,
        max_hosts=32,
    )

    assert any(entry.key == "9.9.9.9:7709" for entry in hosts)
    assert all(entry.family == Family.STANDARD for entry in hosts)


def test_ranking_save_rejects_same_endpoint_cross_family_collision(tmp_path) -> None:
    store = RankingStore(str(tmp_path / "ranking.json"))

    with pytest.raises(ConfigError, match="family collision"):
        store.save(
            [
                HostEntry(host="1.2.3.4", port=7709, family=Family.STANDARD),
                HostEntry(host="1.2.3.4", port=7709, family=Family.F10),
            ]
        )


def test_ranking_update_replaces_cross_family_identity_instead_of_mixing_metrics(tmp_path) -> None:
    store = RankingStore(str(tmp_path / "ranking.json"))
    store.update(
        [
            HostEntry(
                host="1.2.3.4",
                port=7709,
                family=Family.F10,
                rtt_ms=1.0,
                last_ok=1.0,
            )
        ]
    )
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

    entry = store.load()["1.2.3.4:7709"]
    assert entry.family == Family.STANDARD
    assert entry.rtt_ms == 9.0


def test_explicit_host_entry_with_wrong_family_fails_closed() -> None:
    with pytest.raises(ConfigError, match="family 不匹配"):
        resolve_hosts(
            [HostEntry(host="1.2.3.4", family=Family.F10)],
            family=Family.STANDARD,
        )
