from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_precommit_fast_gates_match_blocking_static_contracts() -> None:
    config = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "python -m ruff check tstdx/ tests/ scripts/" in config
    assert "python -m ruff format --check tstdx/ tests/ scripts/" in config
    assert (
        "python -m mypy tstdx/ --ignore-missing-imports --no-error-summary --warn-unused-ignores"
    ) in config
    assert "python -m tstdx.tools.check_originality --strict tstdx/" in config
    assert "python -m tstdx.tools.spec_audit --json --strict" in config
    assert "python scripts/check_docs_links.py" in config


def test_precommit_hygiene_hooks_use_current_supported_major() -> None:
    config = (_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "repo: https://github.com/pre-commit/pre-commit-hooks" in config
    assert "rev: v6.0.0" in config
    assert "rev: v4.6.0" not in config


def test_make_install_provides_precommit_runtime_and_runner() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")

    assert '"pre-commit==4.6.2"' in makefile
    assert "$(PYTHON) -m pre_commit run --all-files" in makefile
