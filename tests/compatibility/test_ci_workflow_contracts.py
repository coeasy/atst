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


def test_main_test_matrix_uses_declared_dev_dependency_ssot() -> None:
    workflow = _workflow("ci.yml")

    assert 'python -m pip install -e ".[all,dev]"' in workflow
    assert "pip install pytest pytest-cov" not in workflow


def test_static_and_auxiliary_test_jobs_use_declared_dev_toolchain() -> None:
    workflow = _workflow("ci.yml")

    assert workflow.count('python -m pip install -e ".[dev]"') >= 4
    assert "pip install ruff" not in workflow
    assert "pip install mypy" not in workflow
    assert "run: pytest tests/test_bridges.py" not in workflow
    assert "run: python -m pytest tests/test_bridges.py" in workflow


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
    assert '"tstdx/py.typed" in archive.namelist()' in workflow
    assert "os: [ubuntu-latest, macos-latest, windows-latest]" in workflow
    assert "python-version: ['3.10', '3.11', '3.12', '3.13']" in workflow
    assert "--only-binary=:all: tstdx" in workflow
    assert "joinpath('py.typed').is_file()" in workflow


def test_artifact_only_smoke_does_not_enable_setup_python_dependency_cache() -> None:
    workflow = _workflow("wheels.yml")
    smoke = workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]

    assert "actions/checkout" not in smoke
    assert "actions/download-artifact@v4" in smoke
    assert "actions/setup-python@v5" in smoke
    assert "cache: 'pip'" not in smoke


def test_release_publishes_once_only_after_artifact_matrix_passes() -> None:
    workflow = _workflow("wheels.yml")

    assert workflow.count("pypa/gh-action-pypi-publish@release/v1") == 1
    assert "needs: [build-dist, smoke-install]" in workflow
    assert "if: github.event_name == 'release' && github.event.action == 'published'" in workflow
    assert "environment: pypi" in workflow
    assert "id-token: write" in workflow


def test_release_docker_reuses_the_same_canonical_python_artifact() -> None:
    workflow = _workflow("wheels.yml")
    docker = workflow.split("  publish-docker:", 1)[1]

    assert "needs: [build-dist, smoke-install]" in docker
    assert "name: python-dist" in docker
    assert "path: release-dist" in docker
    assert "file: Dockerfile.release" in docker
    assert "docker/build-push-action@v6" in docker


def test_release_identity_is_fail_closed_before_build_or_publish() -> None:
    workflow = _workflow("wheels.yml")

    assert "Verify source version matches package metadata" in workflow
    assert "assert tstdx.__version__ == version" in workflow
    assert "Verify release tag matches package version" in workflow
    assert "RELEASE_TAG: ${{ github.event.release.tag_name }}" in workflow
    assert 'expected = f"v{version}"' in workflow
    assert "assert actual == expected" in workflow
    assert "github.event.release.prerelease == false" in workflow


def test_scheduled_live_smoke_reports_real_failure_and_always_emits_junit() -> None:
    workflow = _workflow("live-smoke.yml")

    assert "pull_request:" not in workflow
    assert "continue-on-error" not in workflow
    assert 'python -m pip install -e ".[all,dev]"' in workflow
    assert '-m "network"' in workflow
    assert "--junitxml=reports/live-smoke.xml" in workflow
    assert "if: always()" in workflow
    assert "path: reports/live-smoke.xml" in workflow
