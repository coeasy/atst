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
