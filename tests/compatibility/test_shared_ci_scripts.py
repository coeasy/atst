from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str) -> ModuleType:
    path = _ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"tstdx_contract_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docs_link_checker_accepts_repository_bounded_relative_links(tmp_path: Path) -> None:
    checker = _load_script("check_docs_links.py")
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    (docs / "guide.md").write_text("[root](../README.md)\n", encoding="utf-8")

    assert checker.check_docs_links(docs=docs, root=tmp_path) == []


def test_docs_link_checker_rejects_missing_and_repository_escape(tmp_path: Path) -> None:
    checker = _load_script("check_docs_links.py")
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    (docs / "guide.md").write_text(
        "[missing](missing.md)\n[escape](../../outside.md)\n",
        encoding="utf-8",
    )

    bad = checker.check_docs_links(docs=docs, root=root)

    assert any("missing.md" in item for item in bad)
    assert any("escapes repository" in item for item in bad)


def test_docs_link_checker_scans_root_docs_and_spares_the_changelog(tmp_path: Path) -> None:
    """扫描面要覆盖仓库根的说明文件，同时放过变更日志。

    README 一度整份在射程外（判据只走 ``docs/``），于是它指向已归档计划的死链能在绿灯下
    存活。反向的豁免是给 CHANGELOG 的：它每条写的是**当时**的路径，为绿灯改写历史条目
    等于让这份日志失去证据价值。
    """
    checker = _load_script("check_docs_links.py")
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    # 故意不带 .md：带扩展名的假路径会被"代码里的 docs 指路牌"那条判据当成违约现场
    (root / "README.md").write_text("[gone](docs/gone)\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("[gone](docs/gone)\n", encoding="utf-8")

    bad = checker.check_docs_links(docs=root / "docs", root=root)

    assert any(item.startswith("README.md") for item in bad), bad
    assert not any(item.startswith("CHANGELOG") for item in bad), bad


def test_benchmark_validator_accepts_positive_synthetic_results() -> None:
    benchmark = _load_script("run_benchmark_smoke.py")
    data = {
        "mode": "synthetic",
        "results": {"quotes": {"rows": 10, "rows_per_s": 100.0}},
    }

    benchmark._validate(data, metric="rows_per_s", label="time")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"mode": "live", "results": {"x": {"rows": 1, "rows_per_s": 1}}},
            "synthetic",
        ),
        ({"mode": "synthetic", "results": {}}, "empty"),
        (
            {"mode": "synthetic", "results": {"x": {"rows": 0, "rows_per_s": 1}}},
            "rows",
        ),
        (
            {"mode": "synthetic", "results": {"x": {"rows": 1, "rows_per_s": 0}}},
            "rows_per_s",
        ),
    ],
)
def test_benchmark_validator_fails_closed(data: dict, message: str) -> None:
    benchmark = _load_script("run_benchmark_smoke.py")

    with pytest.raises(SystemExit, match=message):
        benchmark._validate(data, metric="rows_per_s", label="time")
