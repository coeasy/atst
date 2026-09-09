from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _makefile() -> str:
    return (_ROOT / "Makefile").read_text(encoding="utf-8")


def test_install_uses_ci_equivalent_dependency_ssot() -> None:
    makefile = _makefile()

    assert '$(PIP) install -e ".[all,dev]" build twine' in makefile
    assert 'pip install -e ".[all]"' not in makefile
    assert "pip install pytest pytest-cov ruff mypy build" not in makefile


def test_local_test_gate_keeps_ci_coverage_threshold_and_offline_scope() -> None:
    makefile = _makefile()

    assert '--cov-fail-under=77' in makefile
    assert '--cov-report=xml:coverage.xml' in makefile
    assert '-m "not network"' in makefile
    assert '--cov-fail-under=75' not in makefile


def test_marker_targets_use_valid_pytest_marker_syntax() -> None:
    makefile = _makefile()

    assert '--mark=' not in makefile
    assert '-m "golden"' in makefile
    assert '-m "slow"' in makefile


def test_local_gates_include_all_deterministic_specialized_checks() -> None:
    makefile = _makefile()

    gates_line = next(line for line in makefile.splitlines() if line.startswith("gates:"))
    for target in (
        "lint",
        "type-check",
        "test",
        "test-bridges",
        "audit-golden",
        "audit-spec",
        "audit-adversarial",
        "audit-reachability",
        "audit-originality",
        "benchmark-smoke",
        "audit-docs",
        "native-compat",
    ):
        assert target in gates_line

    assert "--warn-unused-ignores" in makefile
    assert "spec_audit --json --strict" in makefile
    assert "--require-kline-categories 0,4,9" in makefile


def test_local_publish_cannot_bypass_trusted_release_workflow() -> None:
    makefile = _makefile()

    assert "Direct local publishing is disabled" in makefile
    assert "twine upload" not in makefile
    assert "@exit 2" in makefile


def test_local_build_uses_safe_smoke_enabled_builder() -> None:
    makefile = _makefile()

    assert "scripts/build_package.py --smoke" in makefile
