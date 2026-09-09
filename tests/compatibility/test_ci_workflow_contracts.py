from __future__ import annotations

import re
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> str:
    return (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_native_gate_has_no_nonexistent_manifest_or_soft_failure() -> None:
    workflow = _workflow("native.yml")

    assert "continue-on-error" not in workflow
    assert "maturin build" not in workflow
    assert "tstdx_native/Cargo.toml" not in workflow
    assert "tests/compatibility/test_native_fallback_contract.py" in workflow
    assert 'assert result["fallback_parity"] is True' in workflow


def test_any_workflow_manifest_path_points_to_a_repository_file() -> None:
    workflows = (_ROOT / ".github" / "workflows").glob("*.yml")
    missing: list[str] = []
    pattern = re.compile(r"--manifest-path\s+([^\s\\]+)")

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            relative = match.group(1).strip("'\"")
            if "${{" in relative:
                continue
            if not (_ROOT / relative).is_file():
                missing.append(f"{path.name}: {relative}")

    assert missing == []


def test_coverage_artifact_is_generated_and_required() -> None:
    workflow = _workflow("ci.yml")

    assert "--cov-fail-under=77" in workflow
    assert "--cov-report=term-missing" in workflow
    assert "--cov-report=xml:coverage.xml" in workflow
    assert "path: coverage.xml" in workflow
    assert "if-no-files-found: error" in workflow


def test_release_builds_once_then_smoke_tests_the_same_universal_wheel() -> None:
    workflow = _workflow("wheels.yml")

    assert "cibuildwheel" not in workflow
    assert "ubuntu-24.04-arm" not in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert "-py3-none-any.whl" in workflow
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "python-version: ['3.10', '3.11', '3.12', '3.13']" in workflow
    assert "python -m pip install --no-index --find-links dist tstdx" in workflow


def test_release_publishes_once_only_after_artifact_matrix_passes() -> None:
    workflow = _workflow("wheels.yml")

    assert workflow.count("pypa/gh-action-pypi-publish@release/v1") == 1
    assert "needs: [build-dist, smoke-install]" in workflow
    assert "if: github.event_name == 'release' && github.event.action == 'published'" in workflow
    assert "environment: pypi" in workflow
    assert "id-token: write" in workflow


def test_scheduled_live_smoke_reports_real_failure_and_always_emits_junit() -> None:
    workflow = _workflow("live-smoke.yml")

    assert "pull_request:" not in workflow
    assert "continue-on-error" not in workflow
    assert '-m "network"' in workflow
    assert "--junitxml=reports/live-smoke.xml" in workflow
    assert "if: always()" in workflow
    assert "path: reports/live-smoke.xml" in workflow
