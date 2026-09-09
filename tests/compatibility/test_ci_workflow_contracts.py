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
    assert 'python -m pip install -e ".[dev]"' in workflow
    assert "pip install pytest" not in workflow
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


def test_blocking_workflows_cancel_only_obsolete_same_event_heads() -> None:
    ci = _workflow("ci.yml")
    native = _workflow("native.yml")

    assert "group: ci-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}" in ci
    assert "group: native-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}" in native
    assert "cancel-in-progress: true" in ci
    assert "cancel-in-progress: true" in native
    assert "timeout-minutes:" in ci
    assert "timeout-minutes: 20" in native


def test_coverage_artifact_is_generated_required_and_preserved_on_failure() -> None:
    workflow = _workflow("ci.yml")
    test_job = workflow.split("  test:", 1)[1].split("  originality:", 1)[0]

    assert "--cov-fail-under=77" in test_job
    assert "--cov-report=term-missing" in test_job
    assert "--cov-report=xml:coverage.xml" in test_job
    assert "path: coverage.xml" in test_job
    assert "if-no-files-found: error" in test_job
    assert "- name: Upload coverage\n        if: always()" in test_job
    assert "retention-days: 14" in test_job


def test_main_ci_uses_shared_deterministic_checks_and_only_monday_schedule() -> None:
    workflow = _workflow("ci.yml")

    assert "cron: '0 8 * * 1'" in workflow
    assert "cron: '0 9 * * 3'" not in workflow
    assert "host-audit:" not in workflow
    assert "python scripts/run_benchmark_smoke.py" in workflow
    assert "python scripts/check_docs_links.py" in workflow


def test_host_audit_is_separate_bounded_strict_operational_workflow() -> None:
    workflow = _workflow("host-audit.yml")

    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "cron: '0 9 * * 3'" in workflow
    assert "group: host-audit" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "--strict" in workflow
    assert "if: always()" in workflow
    assert "actions: write" not in workflow
    assert "audit_report.json" in workflow
    assert "audit_summary.md" in workflow
    assert "retention-days: 14" in workflow


def test_release_builds_once_then_uses_shared_verifier_and_same_wheel_matrix() -> None:
    workflow = _workflow("wheels.yml")

    assert "cibuildwheel" not in workflow
    assert "ubuntu-24.04-arm" not in workflow
    assert "python -m build" in workflow
    assert "python scripts/build_package.py --verify-only --dist-out dist" in workflow
    assert "python -m twine check dist/*" not in workflow
    assert "import zipfile" not in workflow
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


def test_release_assets_reuse_same_canonical_distribution_after_pypi() -> None:
    workflow = _workflow("wheels.yml")
    release_assets = workflow.split("  publish-release-assets:", 1)[1].split(
        "  publish-docker:", 1
    )[0]

    assert "needs: [build-dist, smoke-install, publish-pypi]" in release_assets
    assert "name: python-dist" in release_assets
    assert "contents: write" in release_assets
    assert "wheel_count=" in release_assets
    assert "sdist_count=" in release_assets
    assert 'test "$wheel_count" = 1' in release_assets
    assert 'test "$sdist_count" = 1' in release_assets
    assert "gh release upload" in release_assets
    assert "--clobber" in release_assets
    assert "python -m build" not in release_assets


def test_release_is_serialized_and_all_external_jobs_are_time_bounded() -> None:
    workflow = _workflow("wheels.yml")

    assert "group: wheels-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "retention-days: 14" in workflow


def test_release_docker_reuses_artifact_only_after_publication_surfaces_succeed() -> None:
    workflow = _workflow("wheels.yml")
    docker = workflow.split("  publish-docker:", 1)[1]

    assert "needs: [build-dist, smoke-install, publish-pypi, publish-release-assets]" in docker
    assert "name: python-dist" in docker
    assert "path: release-dist" in docker
    assert "file: Dockerfile.release" in docker
    assert "docker/build-push-action@v6" in docker


def test_release_identity_reuses_shared_source_parser_before_build() -> None:
    workflow = _workflow("wheels.yml")
    build = workflow.split("  build-dist:", 1)[1].split("  smoke-install:", 1)[0]

    assert "Verify release tag matches canonical source identity" in build
    assert "RELEASE_TAG: ${{ github.event.release.tag_name }}" in build
    assert "from scripts.build_package import _declared_versions" in build
    assert "project_version, source_version = _declared_versions()" in build
    assert "assert source_version == project_version" in build
    assert 'expected = f"v{project_version}"' in build
    assert "import re" not in build
    assert "python scripts/build_package.py --verify-only --dist-out dist" in build
    assert "github.event.release.prerelease == false" in workflow


def test_scheduled_live_smoke_is_bounded_truthful_and_always_emits_junit() -> None:
    workflow = _workflow("live-smoke.yml")

    assert "pull_request:" not in workflow
    assert "continue-on-error" not in workflow
    assert "cron: '0 1 * * *'" in workflow
    assert "group: live-smoke" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "timeout-minutes: 30" in workflow
    assert 'python -m pip install -e ".[all,dev]"' in workflow
    assert '-m "network"' in workflow
    assert "--junitxml=reports/live-smoke.xml" in workflow
    assert "if: always()" in workflow
    assert "path: reports/live-smoke.xml" in workflow
    assert "retention-days: 14" in workflow
