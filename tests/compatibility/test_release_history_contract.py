from __future__ import annotations

import re
from pathlib import Path

import atst

_ROOT = Path(__file__).resolve().parents[2]

#: G9：任何一份读者会照着敲的文档都在名单上。判据的对象不是"提没提 PyPI"，
#: 而是"有没有把一条装不通的命令写得像装得通"。
_INSTALL_DOCS = (
    "README.md",
    "docs/quickstart.md",
    "docs/api/README.md",
    "docs/FAQ.md",
    "docs/releases/v1.0.0.md",
)

#: `pip install atst` / `pip install "atst[all]"` / `pip install --upgrade atst==1.0.0`
#: 三种写法同一个形状；`-e ".[dev]"` 与 `".[all]"` 这种从源码装的写法不在其内。
_PYPI_INSTALL = re.compile(r"pip install (?:--upgrade )?[\"']?atst")

#: 页面上必须出现的口径声明（写明本包当前不在 PyPI 上，见各页 G9 段）。
_UNPUBLISHED_MARKER = "不在 PyPI"


def test_no_user_doc_presents_an_unpublished_install_path_as_available() -> None:
    """G9：`pip install atst` 在 PyPI 上没有对应物，文档不能把它写成可用指令。

    实测口径来自 ``reports/g9_pypi_probe.log``（2026-09-22）：
    ``https://pypi.org/pypi/atst/json`` 与 ``/simple/atst/`` 均回 404。一条装不通
    的命令与一条会给出错数的命令是同一类缺陷——差别只在于受害的是新读者。
    真正关闭这一格的是发布动作；本判据保证的是『在发布之前，文档不再替它背书』。
    """
    scanned = 0
    offenders: list[str] = []
    for relative in _INSTALL_DOCS:
        text = (_ROOT / relative).read_text(encoding="utf-8")
        scanned += 1
        if _PYPI_INSTALL.search(text) and _UNPUBLISHED_MARKER not in text:
            offenders.append(relative)

    assert scanned == len(_INSTALL_DOCS), "扫描数与名单不符，判据自身失明"
    assert offenders == [], f"这些文档把未上架的安装路径写成可用：{offenders}"


def test_published_v1_release_history_is_preserved() -> None:
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _ROOT / "docs" / "releases" / "v1.0.0.md"

    assert "## [1.0.0] - 2026-09-09" in changelog
    assert release.is_file()
    release_text = release.read_text(encoding="utf-8")
    assert "v1.0.0" in release_text
    assert "atst-1.0.0-py3-none-any.whl" in release_text
    assert "atst-1.0.0.tar.gz" in release_text


def test_general_docs_distinguish_stable_release_from_development_identity() -> None:
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    assert f"当前 Draft 开发版本：`{atst.__version__}`" in readme
    assert "最新已发布稳定版：`v1.0.0`" in readme
    assert f"当前 `{atst.__version__}` Draft 开发线" in quickstart
    assert "最新已发布稳定版是 `v1.0.0`" in quickstart
    assert f"当前 `{atst.__version__}` Draft 开发线" in api_index
    assert "最新已发布稳定版是 `v1.0.0`" in api_index


def test_current_docs_do_not_reintroduce_cross_provider_fallback_as_default() -> None:
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    forbidden_chain = "TDX 主站 → HTTP Web 源 → 本地 vipdoc → 缓存 → 合成数据"
    assert forbidden_chain not in quickstart
    assert "禁止跨 Provider silent fallback" in quickstart
    assert "禁止跨 Provider silent fallback" in api_index
    assert "legacy compatibility router" in api_index
