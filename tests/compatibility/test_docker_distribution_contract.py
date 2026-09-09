from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _dockerfile() -> str:
    return (_ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_builder_has_full_offline_contract_inputs_and_dev_dependencies() -> None:
    dockerfile = _dockerfile()
    builder = dockerfile.split("FROM python:3.11-slim AS runtime", 1)[0]

    assert 'python -m pip install --no-cache-dir -e ".[all,dev]"' in builder
    assert "COPY tests/ tests/" in builder
    assert "COPY scripts/ scripts/" in builder
    assert "COPY PROTOCOL_SPEC/ PROTOCOL_SPEC/" in builder
    assert "COPY docs/ docs/" in builder
    assert "COPY .github/workflows/ .github/workflows/" in builder
    assert 'python -m pytest tests/ -m "not network"' in builder


def test_runtime_installs_exact_tested_wheel_without_source_rebuild() -> None:
    dockerfile = _dockerfile()
    runtime = dockerfile.split("FROM python:3.11-slim AS runtime", 1)[1]

    assert "python -m build --wheel" in dockerfile
    assert "python -m twine check dist/*.whl" in dockerfile
    assert "COPY --from=builder /build/dist/*.whl /tmp/" in runtime
    assert "python -m pip install --no-cache-dir --no-deps /tmp/*.whl" in runtime
    assert "COPY tstdx/" not in runtime
    assert ".[all]" not in runtime
    assert "python -m pip check" in runtime


def test_docker_context_excludes_repository_and_generated_noise() -> None:
    patterns = {
        line.strip()
        for line in (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".git" in patterns
    assert ".pytest_cache" in patterns
    assert ".mypy_cache" in patterns
    assert ".ruff_cache" in patterns
    assert "coverage.xml" in patterns
    assert "dist" in patterns
    assert "*.whl" in patterns
