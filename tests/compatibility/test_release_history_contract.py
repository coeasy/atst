from __future__ import annotations

from pathlib import Path

import tstdx


_ROOT = Path(__file__).resolve().parents[2]


def test_published_v1_release_history_is_preserved() -> None:
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _ROOT / "docs" / "releases" / "v1.0.0.md"

    assert "## [1.0.0] - 2026-09-09" in changelog
    assert release.is_file()
    release_text = release.read_text(encoding="utf-8")
    assert "v1.0.0" in release_text
    assert "tstdx-1.0.0-py3-none-any.whl" in release_text
    assert "tstdx-1.0.0.tar.gz" in release_text


def test_general_docs_distinguish_stable_release_from_development_identity() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    assert f"当前 Draft 开发版本：`{tstdx.__version__}`" in readme
    assert "最新已发布稳定版：`v1.0.0`" in readme
    assert f"当前 `{tstdx.__version__}` Draft 开发线" in quickstart
    assert "最新已发布稳定版是 `v1.0.0`" in quickstart
    assert f"当前 `{tstdx.__version__}` Draft 开发线" in api_index
    assert "最新已发布稳定版是 `v1.0.0`" in api_index


def test_current_docs_do_not_reintroduce_cross_provider_fallback_as_default() -> None:
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    forbidden_chain = "TDX 主站 → HTTP Web 源 → 本地 vipdoc → 缓存 → 合成数据"
    assert forbidden_chain not in quickstart
    assert "禁止跨 Provider silent fallback" in quickstart
    assert "禁止跨 Provider silent fallback" in api_index
    assert "legacy compatibility router" in api_index
