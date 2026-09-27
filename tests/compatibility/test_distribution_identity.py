from __future__ import annotations

import re
from pathlib import Path

import atst

_ROOT = Path(__file__).resolve().parents[2]


def test_source_version_matches_project_metadata() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', pyproject, flags=re.MULTILINE)

    assert match is not None
    assert atst.__version__ == match.group(1)


def test_supported_python_floor_remains_explicit() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in pyproject
    for minor in ("3.10", "3.11", "3.12", "3.13"):
        assert f'"Programming Language :: Python :: {minor}"' in pyproject


def test_typed_classifier_has_pep561_marker_in_package_tree() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"Typing :: Typed"' in pyproject
    marker = _ROOT / "atst" / "py.typed"
    assert marker.is_file()


def test_dev_backend_floor_matches_declared_build_system_requirement() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = re.findall(r'"hatchling>=(\d+\.\d+)"', pyproject)

    assert requirements == ["1.25", "1.25"]
