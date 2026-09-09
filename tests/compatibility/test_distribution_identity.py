from __future__ import annotations

import re
from pathlib import Path

import tstdx


_ROOT = Path(__file__).resolve().parents[2]


def test_source_version_matches_project_metadata() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', pyproject, flags=re.MULTILINE)

    assert match is not None
    assert tstdx.__version__ == match.group(1)


def test_supported_python_floor_remains_explicit() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in pyproject
    for minor in ("3.10", "3.11", "3.12", "3.13"):
        assert f'"Programming Language :: Python :: {minor}"' in pyproject
