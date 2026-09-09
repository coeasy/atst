from __future__ import annotations

from pathlib import Path

import pytest

import tstdx.transport.speedtest as speedtest_module
from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import HostEntry
from tstdx.transport.speedtest import ProbeResult, rank_hosts, speedtest, speedtest_and_save


def test_speedtest_defaults_to_canonical_family_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = HostEntry(host="127.0.0.1", port=7709, family=Family.F10)
    monkeypatch.setitem(speedtest_module.POOL_BY_FAMILY, Family.F10, (entry,))
    monkeypatch.setattr(
        speedtest_module,
        "probe",
        lambda host, port, **kwargs: ProbeResult(
            host=host,
            port=port,
            family=kwargs["family"],
            ok=True,
            rtt_ms=1.0,
        ),
    )

    results = speedtest(None, family=Family.F10, max_workers=1)

    assert len(results) == 1
    assert results[0].family == Family.F10
    assert results[0].host == "127.0.0.1"


def test_speedtest_rejects_mismatched_host_family_before_network() -> None:
    with pytest.raises(ConfigError, match="family 不匹配"):
        speedtest(
            [HostEntry(host="127.0.0.1", family=Family.F10)],
            family=Family.STANDARD,
        )


def test_rank_hosts_rejects_cross_family_results() -> None:
    with pytest.raises(ConfigError, match="跨 family"):
        rank_hosts(
            [
                ProbeResult(host="1.1.1.1", port=7709, family=Family.STANDARD),
                ProbeResult(host="2.2.2.2", port=7709, family=Family.F10),
            ]
        )


def test_nonstandard_speedtest_and_save_never_constructs_standard_ranking_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [
        ProbeResult(
            host="127.0.0.1",
            port=7709,
            family=Family.F10,
            ok=True,
            rtt_ms=1.0,
        )
    ]

    monkeypatch.setattr(speedtest_module, "speedtest", lambda *_args, **_kwargs: results)

    class ExplodingStore:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("non-standard speedtest must not open V1 RankingStore")

    monkeypatch.setattr(speedtest_module, "RankingStore", ExplodingStore)

    assert speedtest_and_save(family=Family.F10) is results


def test_standard_speedtest_and_save_persists_ranked_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    results = [
        ProbeResult(
            host="127.0.0.1",
            port=7709,
            family=Family.STANDARD,
            ok=True,
            rtt_ms=1.0,
        )
    ]
    saved: list[HostEntry] = []
    monkeypatch.setattr(speedtest_module, "speedtest", lambda *_args, **_kwargs: results)

    class CapturingStore:
        def __init__(self, path: str) -> None:
            assert path == str(tmp_path / "ranking.json")

        def update(self, entries: list[HostEntry]) -> None:
            saved.extend(entries)

    monkeypatch.setattr(speedtest_module, "RankingStore", CapturingStore)

    returned = speedtest_and_save(
        family=Family.STANDARD,
        ranking_file=str(tmp_path / "ranking.json"),
    )

    assert returned is results
    assert len(saved) == 1
    assert saved[0].family == Family.STANDARD
    assert saved[0].rtt_ms == 1.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0.0}, "timeout"),
        ({"samples": 0}, "samples"),
        ({"max_workers": 0}, "max_workers"),
    ],
)
def test_speedtest_rejects_non_positive_limits(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        speedtest([], family=Family.STANDARD, **kwargs)
