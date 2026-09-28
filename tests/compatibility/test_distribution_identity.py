from __future__ import annotations

from pathlib import Path

import atst

_ROOT = Path(__file__).resolve().parents[2]


def test_source_version_has_one_canonical_file() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_source = (_ROOT / "atst" / "_version.py").read_text(encoding="utf-8")
    init_source = (_ROOT / "atst" / "__init__.py").read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]' in pyproject
    assert 'path = "atst/_version.py"' in pyproject
    assert f'__version__ = "{atst.__version__}"' in version_source
    assert "from ._version import __version__" in init_source
    assert '__version__ = "' not in init_source


def test_supported_python_floor_remains_explicit() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.10"' in pyproject
    for minor in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f'"Programming Language :: Python :: {minor}"' in pyproject


def test_typed_classifier_has_pep561_marker_in_package_tree() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"Typing :: Typed"' in pyproject
    marker = _ROOT / "atst" / "py.typed"
    assert marker.is_file()


def test_release_toolchain_is_exactly_pinned() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert pyproject.count('"hatchling==1.32.4"') == 2
    assert '"build==1.6.1"' in pyproject
    assert '"twine==7.0.0"' in pyproject
    assert '"packaging==26.3"' in pyproject
    assert "[tool.hatch.build]" in pyproject
    assert "reproducible = true" in pyproject
