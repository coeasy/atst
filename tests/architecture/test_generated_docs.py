# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""生成物与工作区一致（v2 / W10 §5 的唯一投影守卫）。

投影型文档（Web 源登记表、capability × provider 矩阵、四张服务面、命令账本）的
真值只在运行时；``scripts/gen_docs.py`` 是唯一写者。本判据只做一件事：

    生成物 == 重新生成的结果

改代码不跑 ``make docs-gen`` 会让这里红，而不是让人肉文档悄悄漂移。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _generator():
    spec = importlib.util.spec_from_file_location("atst_gen_docs", ROOT / "scripts" / "gen_docs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projection_docs_match_the_workspace() -> None:
    module = _generator()
    stale = []
    for path, expected in module.render_all().items():
        actual = path.read_text(encoding="utf-8") if path.exists() else ""
        if actual != expected:
            stale.append(path.relative_to(ROOT).as_posix())
    assert stale == [], (
        "以下生成物与工作区不一致，请运行 `make docs-gen` 后提交：\n  " + "\n  ".join(stale)
    )


def test_generation_is_deterministic() -> None:
    module = _generator()
    first = {str(path): content for path, content in module.render_all().items()}
    second = {str(path): content for path, content in module.render_all().items()}
    assert first == second
