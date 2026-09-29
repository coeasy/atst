# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""方案文档的「现状判定」必须与磁盘同真（F-58）。

`docs/archive/plans/REFACTOR_PLAN_V17_CLOSURE.md` §0.1 用的是现在时：它说某条链路"贯通"还是"断链"，读者
（包括问"主体链路是否全部贯通"的人）就按现在时接受。第 33 步之前它一直没跟上代码——两条
❌ 行把 v14 编排信封与 registry 三件套写成现行断链，而那两个层在 Phase 3A/3B 就整层物理
删除了，其"证据"列指向的 `atst/runtime/gateway.py`、`atst/executor_registry.py`、
`atst/provider/router.py` 早在磁盘上不存在；§0.2 的八行"遗留不合理点"则停在"Phase 3–5
处理对象"，八条里已清偿的七条一行判决都没写，读起来像还有八条待办。同族的教训是：既有活
文档门禁只校验反引号里的 `atst.x.y` 点号路径与 README 数字，**带斜杠的文件路径与表格里的
时态都不在射程内**。

两条判据各管一半：

1. §0.1 表体里每个反引号文件路径（`` `a/b/c.py` ``，可带 ``:行号``）必须在磁盘上存在——
   现在时的表不能引用已删除的模块。
2. §0.2 每行必须带一个当前裁决（已清偿 / 已修 / 部分处理 / 待用户决策 / 维持现状）并附日期或
   提交号，否则"遗留"与"已处理"在文档里无法区分。

防盲保险：两节都必须解析出行来（§0.1 ≥4 行、≥5 条路径；§0.2 ≥6 行），解析不出即判门禁自身
失效，而不是静默通过。

第 35 步补第三条判据（F-61）：§0.1 的结论句也出过事，方向相反——它把 Phase 5 第 16 步**已经
跑过**的七格真实网络冒烟写成"真机冒烟尚未执行"。一句凭印象写的"边界"既不挂账也没人复核，
而 §4 验收清单与 §1 执行记录里都写着它已经做过。于是规定：§0.1 结论里每个带圈编号的边界子句
必须点一个 §0.3 里真实存在的 F 号，且其中至少一个仍是开放裁决（未清偿/部分清偿/部分处理/
本轮只登记/本步只登记/待用户决策/维持现状/未处理）——只点已清偿的账，等于把做完的事写成
待办；一个都不点，等于给凭印象的说法发通行证。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "archive" / "plans" / "REFACTOR_PLAN_V17_CLOSURE.md"

#: 反引号里的文件路径，允许尾随 ``:行号``；只认带斜杠的形状——点号路径由
#: ``test_doc_code_consistency.py`` 的 `atst.*` 判据覆盖。
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


def _split_row(line: str) -> list[str]:
    """按 Markdown 表格的行切格，但**行内的 `|` 不算分隔符**。

    账本里两种行内竖线都真实出现过：转义的 `tests/…\\|scripts/…`（F-24），和反引号里的
    正则字面量 `` `a|b` ``（F-43、F-46）。按 `|` 裸切会把这两行的裁决格劈成碎片，碎片里
    没有任何裁决词——于是它们在判据里永远读成"已清偿"，一条挂着的账从分母上消失。
    """
    cells: list[str] = []
    buf: list[str] = []
    code = False
    escaped = False
    for char in line:
        if escaped:
            escaped = False
        elif char == "\\" and not code:
            escaped = True
        elif char == "`":
            code = not code
        elif char == "|" and not code:
            cells.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    cells.append("".join(buf).strip())
    #: 行首的 `|` 切出一个空格子，行尾的 `|` 切出另一个。
    return [cell for cell in cells[1:] if cell != ""] if cells[0] == "" else cells


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _split_row(stripped)
        if not cells or cells[0] in {"#", ""} or set("".join(cells)) <= {"-", ":", " "}:
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
        candidates = [ROOT / rel, ROOT / "atst" / rel]
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


_F_REF = re.compile(r"F-\d+")
#: §0.3 裁决格里代表"这件事还开着"的开头词。刻意不含"已清偿/已修"。
_OPEN_VERDICTS = (
    "未清偿",
    "部分清偿",
    "部分处理",
    "本轮只登记",
    "本步只登记",
    "待用户决策",
    "维持现状",
    "未处理",
)
#: 裁决格必须"读得出来"：第一个加粗跨度里得出现过的裁决词之一。F-24/F-43/F-46 三行曾因
#: 格子里的转义竖线被劈成碎片，碎片里没有任何裁决词——那样一行会被静默读成"已清偿"。
_VERDICT_HEAD = re.compile(
    r"^\s*(?:⏳\s*)?\*\*[^*]*?"
    r"(已清偿|已修|未清偿|部分清偿|部分处理|本轮只登记|本步只登记|待用户决策|维持现状|未处理)"
)
#: 结论里的边界子句以带圈编号起头；没有编号就没有可核对的边界，判判据自身失效。
_CLAUSES = re.compile(r"[①②③④⑤⑥⑦⑧⑨]")


def _finding_verdicts() -> dict[str, str]:
    """§0.3 每行 -> 最后一格（裁决）。分母是账本本身，不是任何抄件。"""
    section = _section("### 0.3", "\n## 1. 分阶段执行计划")
    rows = _table_rows(section)
    assert len(rows) >= 20, f"§0.3 只解析出 {len(rows)} 行，判据自身失效"
    return {cells[0]: cells[-1] for cells in rows}


def _is_open(cell: str) -> bool:
    """裁决格是否以"这件事还开着"的词起头（允许 `⏳ ` 一类前缀）。"""
    head = cell.strip().lstrip("⏳").strip()
    if not head.startswith("**"):
        return False
    body = head[2:]
    return any(body.startswith(word) for word in _OPEN_VERDICTS)


def test_open_boundaries_cite_an_open_finding() -> None:
    """§0.1 结论里的每个边界子句都要挂在一条仍然开着的账上（F-61）。"""
    section = _section("### 0.1 主链路贯通状态", "### 0.2")
    start = section.find("**结论")
    assert start >= 0, "§0.1 没有结论段，判据自身失效"
    verdicts = _finding_verdicts()
    unreadable = sorted(key for key, cell in verdicts.items() if not _VERDICT_HEAD.match(cell))
    assert unreadable == [], f"§0.3 这些行的裁决格读不出来，开放/已清偿无从判断：{unreadable}"

    #: 编号前的引导句不是边界子句，丢掉。
    clauses = [c.strip() for c in _CLAUSES.split(section[start:]) if c.strip()][1:]
    assert len(clauses) >= 2, f"结论里只解析出 {len(clauses)} 个编号子句，判据自身失效"

    offenders: list[str] = []
    for clause in clauses:
        cited = _F_REF.findall(clause)
        if not cited:
            offenders.append(f"没有 F 号：{clause[:40]}…")
            continue
        unknown = [f for f in cited if f not in verdicts]
        if unknown:
            offenders.append(f"§0.3 里没有这些行：{unknown}")
            continue
        if not any(_is_open(verdicts[f]) for f in cited):
            offenders.append(f"只点到已清偿的账：{cited}")
    assert offenders == [], "§0.1 的边界声称没有可复核的开放账目：\n  " + "\n  ".join(offenders)
