from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

#: workflow 命令行里写死的仓内路径（与 Makefile 侧同一判定）。
_REPO_PATH_TOKENS = re.compile(r"(?:tests|scripts|atst)/[A-Za-z0-9_/]+\.py")


def _workflow(name: str) -> str:
    return (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_no_workflow_references_a_nonexistent_repository_path() -> None:
    """workflow 里写死的仓内路径必须存在。

    旧版只查 `--manifest-path`，于是 native.yml 在 v16 Phase 2 删掉 `atst/native.py`
    与它的契约测试之后仍然"合法"。该 job 是 PR 阻塞项：`compileall -q <不存在的路径>`
    **打印 "Can't list" 却退出 0**，静默通过后才由 pytest 以退出码 4 固定失败。
    """
    missing: list[str] = []
    for path in sorted((_ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        for relative in _REPO_PATH_TOKENS.findall(text):
            if not (_ROOT / relative).is_file():
                missing.append(f"{path.name}: {relative}")

    assert missing == [], f"workflow 引用磁盘上不存在的仓内路径：{missing}"


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

    assert (
        "group: ci-${{ github.event_name }}-${{ github.event.pull_request.number || github.ref }}"
        in ci
    )
    assert "cancel-in-progress: true" in ci
    assert "timeout-minutes:" in ci


def test_coverage_artifact_is_generated_required_and_preserved_on_failure() -> None:
    workflow = _workflow("ci.yml")
    test_job = workflow.split("  test:", 1)[1].split("  originality:", 1)[0]

    assert "--cov-fail-under" not in test_job
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
    assert "python-version: ['3.10', '3.11', '3.12', '3.13', '3.14']" in workflow
    assert "--only-binary=:all: atst" in workflow
    assert "joinpath('py.typed').is_file()" in workflow
    assert "ConnectionPool.request.__module__ == 'atst.transport.pool'" in workflow
    assert "AsyncConnectionPool.request.__module__ == 'atst.transport.async_'" in workflow


def test_artifact_only_smoke_does_not_enable_setup_python_dependency_cache() -> None:
    workflow = _workflow("wheels.yml")
    smoke = workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]

    assert "actions/checkout" not in smoke
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in smoke
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in smoke
    assert "cache: 'pip'" not in smoke


def test_release_publishes_once_only_after_draft_release_is_ready() -> None:
    workflow = _workflow("wheels.yml")

    assert workflow.count("pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33") == 1
    assert "needs: [build-dist, prepare-release]" in workflow
    assert "if: vars.PUBLIC_RELEASE == 'true'" in workflow
    assert "environment: pypi" in workflow
    assert "id-token: write" in workflow


def test_release_assets_are_attached_to_a_draft_before_publication() -> None:
    workflow = _workflow("wheels.yml")
    release_assets = workflow.split("  prepare-release:", 1)[1].split(
        "  publish-pypi:", 1
    )[0]

    assert "needs: [build-dist, smoke-install, extras-install, sdist-rebuild]" in release_assets
    assert "name: python-dist" in release_assets
    assert "contents: write" in release_assets
    assert "SHA256SUMS.txt" in release_assets
    assert "gh release create" in release_assets
    assert "--draft" in release_assets
    assert "gh release upload" in release_assets
    assert "--clobber" in release_assets
    assert "python -m build" not in release_assets


def test_release_is_serialized_and_all_external_jobs_are_time_bounded() -> None:
    workflow = _workflow("wheels.yml")

    assert "group: python-release-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 20" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "retention-days: 14" in workflow


def test_release_docker_reuses_artifact_only_after_pypi_succeeds() -> None:
    workflow = _workflow("wheels.yml")
    docker = workflow.split("  publish-docker:", 1)[1].split("  publish-release:", 1)[0]

    assert "needs: [build-dist, prepare-release, publish-pypi]" in docker
    assert "vars.PUBLIC_RELEASE == 'true'" in docker
    assert "name: python-dist" in docker
    assert "path: release-dist" in docker
    assert "file: Dockerfile.release" in docker
    assert "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8" in docker


def test_release_identity_uses_tag_push_and_canonical_version_source() -> None:
    workflow = _workflow("wheels.yml")
    build = workflow.split("  build-dist:", 1)[1].split("  smoke-install:", 1)[0]

    assert "push:" in workflow
    assert "tags:" in workflow
    assert "- 'v*'" in workflow
    assert "Verify tag matches canonical package version" in build
    assert "RELEASE_TAG: ${{ github.ref_name }}" in build
    assert "from scripts.build_package import _declared_version" in build
    assert "version = _declared_version()" in build
    assert 'expected = f"v{version}"' in build
    assert "python scripts/build_package.py --verify-only --dist-out dist" in build
    assert "release:" not in workflow.split("on:", 1)[1].split("permissions:", 1)[0]


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


#: G4 的核心链探针：流水线上唯一真发 7709 包的那族判据。
_CORE_CHAIN_PROBE = _ROOT / "tests" / "live" / "test_tdx_core_chain.py"


def test_live_smoke_runs_a_7709_probe_inside_the_trading_session() -> None:
    """调度必须落在 A 股盘中，并且盘中有可执行的 7709 判据（G4）。

    只有 01:00 UTC（北京 09:00，开盘前）的调度照不到盘中：那一刻既没有"正在形成的
    分钟 K 线"，`quotes` 给的也只是昨收。探针本身在 ``tests/live/``，被 ``-m
    network`` 选中——这两件事都由本判据钉住，缺一条就红。
    """
    workflow = _workflow("live-smoke.yml")

    assert "cron: '30 2 * * 1-5'" in workflow  # 02:30 UTC = 北京 10:30，上午时段

    assert _CORE_CHAIN_PROBE.is_file(), "7709 核心链探针被删除，live-smoke 将无声退化为纯 web 探针"
    probe = _CORE_CHAIN_PROBE.read_text(encoding="utf-8")
    assert "pytestmark = pytest.mark.network" in probe
    assert 'provider="tdx"' in probe, "探针未钉住 tdx，可能测到的是别的 Provider"
    for capability in ("security_count", "bars", "quotes", "snapshot"):
        assert capability in probe, f"探针漏了核心链的一格：{capability}"


def test_the_7709_probe_fails_instead_of_skipping_when_the_chain_is_down() -> None:
    """探针的每一条 ``pytest.skip`` 都必须由"此刻不适用"触发，而不是由异常触发。

    把网络失败写成 skip 是 G4 的原始形状：主站全体下线时流水线仍然全绿。这里按
    AST 走，抓的是"异常处理块里的 skip"——那种 skip 与链路健康无关，只与运行环境
    有关，正是让探针变成安慰剂的写法。
    """
    tree = ast.parse(_CORE_CHAIN_PROBE.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }

    def is_skip(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "skip"
        )

    def guarded_by_clock(node: ast.AST) -> bool:
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.If) and "in_trading_session" in ast.unparse(parent.test):
                return True
            parent = parents.get(parent)
        return False

    skipped_from_handlers = [
        node.lineno
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler)
        for node in ast.walk(handler)
        if is_skip(node)
    ]
    skips = [node for node in ast.walk(tree) if is_skip(node)]

    assert skips, "探针一条 skip 也没有——休市时那条盘中断言怎么办？"
    assert all(guarded_by_clock(node) for node in skips), (
        "探针出现了不由交易时段门控的 skip："
        f"{[node.lineno for node in skips if not guarded_by_clock(node)]}"
    )
    assert skipped_from_handlers == [], (
        f"探针把网络失败吞成了 skip（行 {skipped_from_handlers}）——主站下线时这条流水线必须红"
    )



def test_release_verifies_all_extras_and_sdist_rebuild_before_draft_release() -> None:
    workflow = _workflow("wheels.yml")

    assert "  extras-install:" in workflow
    assert "atst[all]" in workflow
    assert "python -m pip check" in workflow
    assert "  sdist-rebuild:" in workflow
    assert "python -m pip wheel --no-deps --wheel-dir rebuilt dist/*.tar.gz" in workflow
    assert "SHA256SUMS.txt" in workflow


def test_release_is_public_only_after_external_surfaces_finish() -> None:
    workflow = _workflow("wheels.yml")
    publish = workflow.split("  publish-release:", 1)[1]

    assert "needs: [build-dist, prepare-release, publish-pypi, publish-docker]" in publish
    assert "always()" in publish
    assert "gh release edit" in publish
    assert "--draft=false" in publish


def test_release_retry_is_idempotent_and_hash_verified() -> None:
    workflow = _workflow("wheels.yml")

    assert "scripts/check_pypi_release.py" in workflow
    assert "already_published" in workflow
    assert "gh release download" in workflow
    assert "sha256sum -c SHA256SUMS.txt" in workflow
    assert "cmp dist/SHA256SUMS.txt published/SHA256SUMS.txt" in workflow
    assert "steps.pypi-state.outputs.exists != 'true'" in workflow


def test_prerelease_detection_uses_pep440_not_tag_punctuation() -> None:
    workflow = _workflow("wheels.yml")
    build = workflow.split("  build-dist:", 1)[1].split("  smoke-install:", 1)[0]

    assert "from packaging.version import Version" in build
    assert "parsed.is_prerelease or parsed.is_devrelease" in build
    assert "contains(github.ref_name, '-')" not in workflow
    assert "needs.build-dist.outputs.is_prerelease == 'false'" in workflow


def test_release_source_must_be_main_reachable_and_repasses_deterministic_gates() -> None:
    workflow = _workflow("wheels.yml")
    source = workflow.split("  release-source:", 1)[1].split("  build-dist:", 1)[0]
    build = workflow.split("  build-dist:", 1)[1].split("  smoke-install:", 1)[0]

    assert "fetch-depth: 0" in source
    assert 'git fetch --no-tags origin main:refs/remotes/origin/main' in source
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in source
    assert 'python -m pip install -e ".[all,dev]"' in source
    assert "run: make gates" in source
    assert "needs: release-source" in build


def test_release_actions_are_immutable_sha_pinned() -> None:
    workflow = _workflow("wheels.yml")
    forbidden = (
        "actions/checkout@v",
        "actions/setup-python@v",
        "actions/upload-artifact@v",
        "actions/download-artifact@v",
        "pypa/gh-action-pypi-publish@release/",
        "docker/setup-qemu-action@v",
        "docker/setup-buildx-action@v",
        "docker/login-action@v",
        "docker/metadata-action@v",
        "docker/build-push-action@v",
    )
    assert all(token not in workflow for token in forbidden)


def test_sdist_release_docs_are_not_version_hardcoded() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"docs/releases/*.md"' in pyproject
    assert '"docs/releases/v1.0.0.md"' not in pyproject
