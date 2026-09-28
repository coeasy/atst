from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BUILD_TOOL_INSTALL = 'python -m pip install --no-cache-dir -e ".[all,dev]" build twine'


def _dockerfile() -> str:
    return (_ROOT / "Dockerfile").read_text(encoding="utf-8")


def _release_dockerfile() -> str:
    return (_ROOT / "Dockerfile.release").read_text(encoding="utf-8")


def _patterns(name: str) -> set[str]:
    return {
        line.strip()
        for line in (_ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_builder_uses_complete_filtered_repository_contract() -> None:
    dockerfile = _dockerfile()
    builder = dockerfile.split("FROM python:3.11-slim AS runtime", 1)[0]

    assert "COPY . ." in builder
    assert "COPY tests/ tests/" not in builder
    assert "COPY scripts/ scripts/" not in builder
    assert "COPY .github/workflows/ .github/workflows/" not in builder
    assert _BUILD_TOOL_INSTALL in builder
    assert 'python -m pytest tests/ -m "not network"' in builder


def test_packaging_validators_are_installed_before_builder_invokes_build() -> None:
    dockerfile = _dockerfile()
    install_at = dockerfile.index(_BUILD_TOOL_INSTALL)
    build_at = dockerfile.index("python -m build --wheel")
    twine_at = dockerfile.index("python -m twine check dist/*.whl")

    assert install_at < build_at < twine_at


def test_runtime_installs_exact_tested_wheel_without_source_rebuild() -> None:
    dockerfile = _dockerfile()
    runtime = dockerfile.split("FROM python:3.11-slim AS runtime", 1)[1]

    assert "python -m build --wheel" in dockerfile
    assert "python -m twine check dist/*.whl" in dockerfile
    assert "COPY --from=builder /build/dist/*.whl /tmp/" in runtime
    assert "python -m pip install --no-cache-dir --no-deps /tmp/*.whl" in runtime
    assert "COPY atst/" not in runtime
    assert ".[all]" not in runtime
    assert "python -m pip check" in runtime


def test_release_image_has_no_build_stage_and_consumes_only_downloaded_wheel() -> None:
    dockerfile = _release_dockerfile()

    assert dockerfile.count("FROM ") == 1
    assert "COPY release-dist/*.whl /tmp/" in dockerfile
    assert "python -m build" not in dockerfile
    assert "COPY atst/" not in dockerfile
    assert "python -m pip install --no-cache-dir --no-deps /tmp/*.whl" in dockerfile
    assert 'test "$count" -eq 1' in dockerfile
    assert "python -m pip check" in dockerfile


def test_source_docker_context_excludes_noise_and_release_artifacts() -> None:
    patterns = _patterns(".dockerignore")

    assert ".git" in patterns
    assert ".pytest_cache/" in patterns
    assert ".mypy_cache/" in patterns
    assert ".ruff_cache/" in patterns
    assert "coverage.xml" in patterns
    assert "dist/" in patterns
    assert "release-dist/" in patterns
    assert "*.whl" in patterns
    assert "*.tar.gz" in patterns
    assert "benches/results/ci_smoke.json" in patterns
    assert "benches/results/ci_time_smoke.json" in patterns
    assert "benches/results/" not in patterns


def test_release_docker_context_contains_only_dockerfile_and_canonical_wheel() -> None:
    patterns = _patterns("Dockerfile.release.dockerignore")

    assert "**" in patterns
    assert "!Dockerfile.release" in patterns
    assert "!release-dist/" in patterns
    assert "!release-dist/*.whl" in patterns
    assert all("atst/" not in pattern for pattern in patterns)
    assert all("tests/" not in pattern for pattern in patterns)


def test_release_image_base_is_digest_pinned() -> None:
    dockerfile = _release_dockerfile()
    first_from = next(line for line in dockerfile.splitlines() if line.startswith("FROM "))

    assert "python:3.11-slim@sha256:" in first_from
    assert first_from.endswith(" AS runtime")
