from __future__ import annotations

import re
from pathlib import Path

import atst

_ROOT = Path(__file__).resolve().parents[2]

#: G9：任何一份读者会照着敲的当前文档都在名单上。判据的对象不是"提没提 PyPI"，
#: 而是"页面上的安装路径与 PyPI 事实是否一致"。
_CURRENT_INSTALL_DOCS = (
    "README.md",
    "docs/quickstart.md",
    "docs/api/README.md",
    "docs/FAQ.md",
)

#: `pip install atst` / `pip install "atst[all]"` / `pip install --upgrade atst==1.0.0`
#: 三种写法同一个形状；`-e ".[dev]"` 与 `".[all]"` 这种从源码装的写法不在其内。
_PYPI_INSTALL = re.compile(r"pip install (?:--upgrade )?[\"']?atst")

#: 页面必须写明的当前事实（2026-11-08：PyPI 已发布 1.6.1）。
_PUBLISHED_MARKER = "已发布"

#: 已经过时的表述；出现在当前安装文档里就是误导。
_STALE_MARKERS = ("不在 PyPI", '{"message": "Not Found"}', "PyPI 上没有")


def test_current_install_docs_describe_the_published_package() -> None:
    """G9：当前安装文档必须与 PyPI 事实同口径。

    2026-09-22 时 ``https://pypi.org/pypi/atst/json`` 回 404，判据要求文档写明
    「不在 PyPI」；2026-11-08 实测项目已存在、最新 ``1.6.1``、``pip install atst``
    可用，判据随事实翻到「已发布」，并禁止旧口径回流（判据与文档必须一起动）。
    """
    offenders: list[str] = []
    for relative in _CURRENT_INSTALL_DOCS:
        text = (_ROOT / relative).read_text(encoding="utf-8")
        if _PUBLISHED_MARKER not in text:
            offenders.append(f"{relative}: missing '已发布'")
        for stale in _STALE_MARKERS:
            if stale in text:
                offenders.append(f"{relative}: stale marker {stale!r}")

    assert offenders == [], f"当前安装文档与 PyPI 事实不一致：{offenders}"

    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    for relative, text in (("README.md", readme), ("docs/quickstart.md", quickstart)):
        assert _PYPI_INSTALL.search(text), f"{relative} 未给出可执行的 pip install atst"


def test_readme_links_render_on_pypi() -> None:
    """PyPI 不解析相对路径；README 里的相对链接在项目页会全部 404。"""

    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)]+)\)", readme)
    relative = [
        link for link in links if not link.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert relative == [], f"README 含 PyPI 页面无法解析的相对链接：{relative}"


def test_v1_release_identity_is_preserved() -> None:
    """v1.0.0 的发行身份：CHANGELOG 章节 + 发布说明 + canonical 产物名。

    2026-09-28 之前这里断言的是「发布候选」「尚未实际发布」——因为那时 GitHub Release
    真的还不存在（此前每一次运行都被 GitHub Actions 计费拦在"job 未启动"）。标签触发的
    流水线跑通并发布 `v1.0.0` 之后，这一格随事实翻到「已发布」；判据与文档必须**一起**动，
    否则门禁就是在替一句已经过时的话背书。
    """
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    release = _ROOT / "docs" / "releases" / "v1.0.0.md"

    assert "## [1.0.0] - 2026-09-28" in changelog
    assert "## Historical tstdx baseline - 2026-09-09" in changelog
    assert release.is_file()
    release_text = release.read_text(encoding="utf-8")
    assert "v1.0.0" in release_text
    assert "已发布" in release_text
    assert "PyPI 尚未发布" in release_text
    assert "atst-1.0.0-py3-none-any.whl" in release_text
    assert "atst-1.0.0.tar.gz" in release_text


def test_general_docs_report_the_actual_released_state() -> None:
    """三处读者入口对「发布到哪了」的口径必须与事实一致。

    事实（2026-11-08）：PyPI 项目 `atst` 已存在，最新 `1.6.1`；GitHub Release `v1.6.1`
    已发布。所以三页都要写明「已发布」，且**不得**再出现「不在 PyPI」——
    只写其一就是另一种形式的不如实。
    """
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    assert f"当前版本：`{atst.__version__}`" in readme
    assert "已发布" in readme and "不在 PyPI" not in readme
    assert f"当前 `{atst.__version__}`" in quickstart
    assert "已发布" in quickstart and "不在 PyPI" not in quickstart
    assert f"当前 `{atst.__version__}`" in api_index
    assert "已发布" in api_index and "不在 PyPI" not in api_index


def test_current_docs_report_the_actual_pypi_version() -> None:
    """读者入口说"PyPI 最新"时必须跟当前版本，不能停在上一版。

    v2.2.0 上线后，README / quickstart / api README / FAQ / release-publishing 五份
    当前口径的文档都还写着「最新 2.1.0」。这类句子不会让任何代码测试失败，却会让
    `pip install atst` 的读者以为自己装错了版本；版本发布后必须随事实翻篇。
    """
    latest = re.compile(r"最新(?:版本)?\s*\*{0,2}`" + re.escape(atst.__version__) + r"`\*{0,2}")
    offenders: list[str] = []
    for relative in (
        "README.md",
        "docs/quickstart.md",
        "docs/api/README.md",
        "docs/FAQ.md",
        "docs/release-publishing.md",
    ):
        text = (_ROOT / relative).read_text(encoding="utf-8")
        if not latest.search(text):
            offenders.append(relative)

    assert offenders == [], f"当前文档没有报出 PyPI 最新版本 {atst.__version__}：{offenders}"


def test_current_docs_do_not_reintroduce_cross_provider_fallback_as_default() -> None:
    quickstart = (_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    api_index = (_ROOT / "docs" / "api" / "README.md").read_text(encoding="utf-8")

    forbidden_chain = "TDX 主站 → HTTP Web 源 → 本地 vipdoc → 缓存 → 合成数据"
    assert forbidden_chain not in quickstart
    assert "禁止跨 Provider silent fallback" in quickstart
    assert "禁止跨 Provider silent fallback" in api_index
    assert "legacy compatibility router" in api_index


def test_release_candidate_has_a_closed_changelog_identity() -> None:
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert changelog.count("## [1.0.0] - 2026-09-28") == 1
    assert changelog.count("## [Unreleased]") == 1
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("## [1.0.0] - 2026-09-28", 1)[0]
    assert "暂无。" in unreleased


def test_unpublished_release_candidate_does_not_claim_stable_trove_status() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"Development Status :: 4 - Beta"' in pyproject
    assert '"Development Status :: 5 - Production/Stable"' not in pyproject
