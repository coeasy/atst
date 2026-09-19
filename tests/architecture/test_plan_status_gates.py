# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""方案文档的「现状判定」必须与磁盘同真（F-58）。

`docs/REFACTOR_PLAN_V17_CLOSURE.md` §0.1 用的是现在时：它说某条链路"贯通"还是"断链"，读者
（包括问"主体链路是否全部贯通"的人）就按现在时接受。第 33 步之前它一直没跟上代码——两条
❌ 行把 v14 编排信封与 registry 三件套写成现行断链，而那两个层在 Phase 3A/3B 就整层物理
删除了，其"证据"列指向的 `tstdx/runtime/gateway.py`、`tstdx/executor_registry.py`、
`tstdx/provider/router.py` 早在磁盘上不存在；§0.2 的八行"遗留不合理点"则停在"Phase 3–5
处理对象"，八条里已清偿的七条一行判决都没写，读起来像还有八条待办。同族的教训是：既有活
文档门禁只校验反引号里的 `tstdx.x.y` 点号路径与 README 数字，**带斜杠的文件路径与表格里的
时态都不在射程内**。

两条判据各管一半：

1. §0.1 表体里每个反引号文件路径（`` `a/b/c.py` ``，可带 ``:行号``）必须在磁盘上存在——
   现在时的表不能引用已删除的模块。
2. §0.2 每行必须带一个当前裁决（已清偿 / 已修 / 部分处理 / 待用户决策 / 维持）并附日期或
   提交号，否则"遗留"与"已处理"在文档里无法区分。

防盲保险：两节都必须解析出行来（§0.1 ≥4 行、≥5 条路径；§0.2 ≥6 行），解析不出即判门禁自身
失效，而不是静默通过。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "REFACTOR_PLAN_V17_CLOSURE.md"

#: 反引号里的文件路径，允许尾随 ``:行号``；只认带斜杠的形状——点号路径由
#: ``test_doc_code_consistency.py`` 的 `tstdx.*` 判据覆盖。
_CITED_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.py)(?::\d+)?`")
#: 裁决必须是格子的**开头加粗标记**，不是行文里出现的任意"已删除/已修"字样——变异 M2 实测：
#: 放宽到子串时，一句"不再依赖已删除的信封线"就能把判据糊过去。
_VERDICT = re.compile(r"^\*\*(已清偿|已修|部分处理|待用户决策|维持现状)\*\*")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}|`[0-9a-f]{7,40}`")


def _section(start_title: str, end_title: str) -> str:
    text = PLAN.read_text(encoding="utf-8")
    assert start_title in text, f"方案文档里找不到 {start_title}，门禁失效"
    start = text.index(start_title)
    rest = text[start + len(start_title) :]
    end = rest.index(end_title) if end_title in rest else len(rest)
    return rest[:end]


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells[0] in {"#", ""} or set("".join(cells)) <= {"-", ":", " "}:
            continue
        rows.append(cells)
    return rows


def test_status_section_only_cites_paths_that_exist() -> None:
    """§0.1 是现在时：它引用的每个文件路径必须仍在磁盘上。"""
    section = _section("### 0.1 主链路贯通状态", "### 0.2")
    rows = _table_rows(section)
    assert len(rows) >= 4, f"§0.1 只解析出 {len(rows)} 行表格，判据自身失效"

    cited = _CITED_PATH.findall(section)
    assert cited, "§0.1 里解析不出任何文件路径，判据自身失效"

    offenders: list[str] = []
    for raw in sorted(set(cited)):
        rel = raw.split(":", 1)[0]
        candidates = [ROOT / rel, ROOT / "tstdx" / rel]
        if not any(candidate.exists() for candidate in candidates):
            offenders.append(rel)
    assert offenders == [], f"§0.1 引用了磁盘上不存在的模块：{offenders}"


def test_legacy_list_rows_carry_a_verdict_and_a_stamp() -> None:
    """§0.2 的"遗留不合理点"不能停在问题陈述上：每行都要给当前裁决与时间/提交号。"""
    section = _section("### 0.2 遗留不合理点", "### 0.3")
    rows = _table_rows(section)
    assert len(rows) >= 6, f"§0.2 只解析出 {len(rows)} 行表格，判据自身失效"

    offenders: list[str] = []
    for cells in rows:
        verdict_cell = cells[-1]
        if not _VERDICT.search(verdict_cell) or not _STAMP.search(verdict_cell):
            offenders.append(cells[0])
    assert offenders == [], f"§0.2 这些行没有当前裁决（或缺日期/提交号）：{offenders}"


@pytest.mark.parametrize("title", ["### 0.1 主链路贯通状态", "### 0.2 遗留不合理点"])
def test_status_sections_are_still_present(title: str) -> None:
    """两节标题改名会让上面两条判据静默失效，这里把它显式钉住。"""
    assert title in PLAN.read_text(encoding="utf-8")
