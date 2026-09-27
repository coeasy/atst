from __future__ import annotations

import json
from pathlib import Path

import pytest

from atst.errors import ConfigError
from atst.protocol.commands import Family
from atst.transport.hosts import HostEntry, RankingStore, parse_server, resolve_hosts


def test_explicit_empty_server_selector_does_not_restore_default_pool() -> None:
    with pytest.raises(ConfigError, match="explicit servers 不能为空"):
        resolve_hosts([], family=Family.STANDARD)


def test_config_shaped_empty_servers_preserve_unset_semantics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", "1.2.3.4:7709")

    resolved = resolve_hosts(
        [],
        family=Family.STANDARD,
        ranking_file=str(tmp_path / "ranking.json"),
        use_ranking=False,
    )

    assert [entry.key for entry in resolved] == ["1.2.3.4:7709"]


def test_invalid_environment_selector_does_not_fall_back_to_builtin_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", "1.2.3.4:not-a-port")

    with pytest.raises(ConfigError, match="ATST_HOSTS 条目无效"):
        resolve_hosts(None, family=Family.STANDARD)


def test_environment_selector_rejects_one_bad_token_in_mixed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", "1.2.3.4:7709 bad:not-a-port")

    with pytest.raises(ConfigError, match="bad:not-a-port"):
        resolve_hosts(None, family=Family.STANDARD)


def test_environment_selector_rejects_empty_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", ":7709")

    with pytest.raises(ConfigError, match="host 为空"):
        resolve_hosts(None, family=Family.STANDARD)


def test_blank_environment_value_remains_equivalent_to_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", "   ")

    resolved = resolve_hosts(None, family=Family.F10, use_ranking=False)

    assert resolved
    assert all(entry.family == Family.F10 for entry in resolved)


def test_explicit_selector_rejects_duplicate_canonical_endpoint_before_truncation() -> None:
    with pytest.raises(ConfigError, match="重复 canonical endpoint"):
        resolve_hosts(
            ["1.2.3.4:7709", " 1.2.3.4:7709 "],
            family=Family.STANDARD,
            use_ranking=False,
            max_hosts=1,
        )


def test_environment_selector_rejects_duplicate_endpoint_before_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATST_HOSTS", "1.2.3.4:7709 1.2.3.4:7709")

    with pytest.raises(ConfigError, match="重复 canonical endpoint"):
        resolve_hosts(
            None,
            family=Family.STANDARD,
            use_ranking=False,
            max_hosts=1,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "host.example:0",
        "host.example:65536",
        "host.example:not-a-port",
        "host example:7709",
        "https://host.example:7709",
    ],
)
def test_parse_server_rejects_invalid_string_endpoints(endpoint: str) -> None:
    with pytest.raises(ConfigError):
        parse_server(endpoint)


def test_parse_server_rejects_mapping_without_host() -> None:
    with pytest.raises(ConfigError, match="缺少 host"):
        parse_server({"port": 7709})


def test_parse_server_rejects_non_boolean_verification_provenance() -> None:
    with pytest.raises(ConfigError, match="verified 必须是 bool"):
        parse_server({"host": "1.2.3.4", "verified": 1})


def test_parse_server_supports_bracketed_ipv6_with_explicit_port() -> None:
    entry = parse_server("[::1]:7727", family=Family.EXTENDED)

    assert entry.host == "::1"
    assert entry.port == 7727
    assert entry.family == Family.EXTENDED
    assert entry.key == "[::1]:7727"


def test_parse_server_supports_bare_ipv6_only_with_default_port() -> None:
    entry = parse_server("::1", family=Family.STANDARD)

    assert entry.host == "::1"
    assert entry.port == 7709
    assert entry.key == "[::1]:7709"


def test_resolve_hosts_rejects_ambiguous_ranking_sources(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="不能同时指定"):
        resolve_hosts(
            ["1.2.3.4:7709"],
            ranking=RankingStore(tmp_path / "one.json"),
            ranking_file=str(tmp_path / "two.json"),
        )


@pytest.mark.parametrize(
    "entry",
    [
        HostEntry(host="1.2.3.4", rtt_ms=float("nan")),
        HostEntry(host="1.2.3.4", rtt_ms=-1.0),
        HostEntry(host="1.2.3.4", failures=-1),
        HostEntry(host="1.2.3.4", circuit="unknown"),
        HostEntry(host="1.2.3.4", last_error=object()),
    ],
)
def test_ranking_store_rejects_invalid_runtime_observations(
    tmp_path: Path,
    entry: HostEntry,
) -> None:
    with pytest.raises(ConfigError):
        RankingStore(tmp_path / "ranking.json").save([entry])


def test_ranking_store_rejects_duplicate_endpoint_rows(tmp_path: Path) -> None:
    store = RankingStore(tmp_path / "ranking.json")

    with pytest.raises(ConfigError, match="重复 endpoint"):
        store.update(
            [
                HostEntry(host="1.2.3.4", port=7709, rtt_ms=1.0),
                HostEntry(host="1.2.3.4", port=7709, rtt_ms=2.0),
            ]
        )


def test_ranking_store_top_rejects_non_positive_limit(tmp_path: Path) -> None:
    store = RankingStore(tmp_path / "ranking.json")

    with pytest.raises(ConfigError, match="正整数"):
        store.top(0)


def test_ranking_load_drops_invalid_rows_without_promoting_them(tmp_path: Path) -> None:
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
                        "rtt_ms": -5.0,
                    },
                    "5.6.7.8:7709": {
                        "host": "5.6.7.8",
                        "port": 7709,
                        "family": Family.STANDARD,
                        "rtt_ms": 5.0,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = RankingStore(path).load()

    assert list(loaded) == ["5.6.7.8:7709"]
    assert loaded["5.6.7.8:7709"].rtt_ms == 5.0


def test_ranking_load_treats_non_mapping_entries_as_invalid_cache(tmp_path: Path) -> None:
    path = tmp_path / "ranking.json"
    path.write_text(
        json.dumps({"version": RankingStore.VERSION, "entries": ["not", "a", "mapping"]}),
        encoding="utf-8",
    )

    assert RankingStore(path).load() == {}
