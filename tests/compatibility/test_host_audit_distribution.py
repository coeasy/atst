from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tstdx.cli.cmds_hosts import _cmd_hosts_audit
from tstdx.protocol.commands import Family
from tstdx.tools import host_audit
from tstdx.transport.hosts import HostEntry
from tstdx.transport.speedtest import ProbeResult


_ROOT = Path(__file__).resolve().parents[2]


def test_public_cli_host_audit_delegates_to_installed_package_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_main(argv: list[str] | None = None) -> int:
        captured.extend(argv or [])
        return 7

    monkeypatch.setattr(host_audit, "main", fake_main)
    args = argparse.Namespace(
        family=[Family.STANDARD, Family.F10],
        timeout=1.5,
        samples=2,
        workers=3,
        report="audit.json",
        markdown="audit.md",
        ranking_file="ranking.json",
        hosts_file="hosts.json",
        quiet=True,
        strict=True,
        no_save_ranking=True,
    )

    assert _cmd_hosts_audit(args) == 7
    assert captured == [
        "--family",
        Family.STANDARD,
        "--family",
        Family.F10,
        "--timeout=1.5",
        "--samples=2",
        "--workers=3",
        "--report=audit.json",
        "--markdown=audit.md",
        "--ranking-file=ranking.json",
        "--hosts-file=hosts.json",
        "--quiet",
        "--strict",
        "--no-save-ranking",
    ]


def test_source_host_audit_script_is_only_a_package_wrapper() -> None:
    script = (_ROOT / "scripts" / "audit_hosts.py").read_text(encoding="utf-8")

    assert "from tstdx.tools.host_audit import" in script
    assert "RankingStore" not in script
    assert "ThreadPoolExecutor" not in script
    assert "def audit_family(" not in script


def test_no_save_ranking_never_constructs_ranking_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_audit_family(family: str, **_: object) -> host_audit.FamilyAudit:
        return host_audit.FamilyAudit(
            family=family,
            total=1,
            healthy=1,
            results=[
                {
                    "host": "127.0.0.1",
                    "port": 7709,
                    "family": family,
                    "ok": True,
                    "connect_ms": 1.0,
                    "rtt_ms": 2.0,
                    "error": "",
                }
            ],
        )

    class ExplodingRankingStore:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("dry-run must not construct RankingStore")

    monkeypatch.setattr(host_audit, "audit_family", fake_audit_family)
    monkeypatch.setattr(host_audit, "RankingStore", ExplodingRankingStore)

    report = host_audit.audit_all(
        families=[Family.STANDARD],
        progress=False,
        save_ranking=False,
    )

    assert report.ranking_saved is False
    assert "dry-run: ranking_file 未写入" in report.notes


def test_json_family_override_is_rebucketed_to_explicit_family(tmp_path: Path) -> None:
    source = tmp_path / "hosts.json"
    source.write_text(
        json.dumps(
            {
                Family.STANDARD: [
                    {
                        "host": "1.2.3.4",
                        "port": 7709,
                        "family": Family.F10,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    loaded = host_audit.load_external_hosts(source)

    assert Family.STANDARD not in loaded
    assert [entry.family for entry in loaded[Family.F10]] == [Family.F10]
    assert [entry.host for entry in loaded[Family.F10]] == ["1.2.3.4"]


def test_reachable_without_rtt_is_degraded_without_invented_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = HostEntry(host="127.0.0.1", port=7709, family=Family.STANDARD)
    monkeypatch.setitem(host_audit.POOL_BY_FAMILY, Family.STANDARD, (entry,))
    monkeypatch.setattr(
        host_audit,
        "probe",
        lambda *_args, **_kwargs: ProbeResult(
            host=entry.host,
            port=entry.port,
            family=entry.family,
            ok=True,
            rtt_ms=None,
        ),
    )

    audit = host_audit.audit_family(Family.STANDARD, progress=False)

    assert audit.healthy == 0
    assert audit.degraded == 1
    assert audit.offline == 0
    assert audit.best_rtt_ms is None
    assert audit.worst_ok_rtt_ms is None
    assert any("without RTT evidence" in note for note in audit.notes)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0.0}, "timeout"),
        ({"samples": 0}, "samples"),
        ({"max_workers": 0}, "max_workers"),
    ],
)
def test_programmatic_audit_rejects_non_positive_limits(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        host_audit.audit_family(Family.STANDARD, progress=False, **kwargs)


def test_cli_parser_rejects_non_positive_worker_count() -> None:
    with pytest.raises(SystemExit) as raised:
        host_audit._parser().parse_args(["--workers", "0"])

    assert raised.value.code == 2
