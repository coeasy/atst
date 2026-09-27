from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _gitignore_patterns() -> set[str]:
    return {
        line.strip()
        for line in (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_current_release_and_operational_scratch_are_ignored() -> None:
    patterns = _gitignore_patterns()

    for expected in (
        "release-dist/",
        "*.whl",
        "*.tar.gz",
        "reports/",
        "audit_report.json",
        "audit_summary.md",
        "host_audit_report.json",
        "benches/results/ci_smoke.json",
        "benches/results/ci_time_smoke.json",
    ):
        assert expected in patterns


def test_tracked_benchmark_baselines_are_not_hidden_by_broad_ignore() -> None:
    patterns = _gitignore_patterns()

    assert "benches/results/" not in patterns
    assert "benches/" not in patterns


def test_retired_native_source_tree_is_not_documented_as_build_output() -> None:
    text = (_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "atst_native/target" not in text
    assert "atst_native/Cargo.lock" not in text
