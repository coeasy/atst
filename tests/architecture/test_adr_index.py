"""``docs/adr/`` 索引门禁：编号、链接、状态行三者必须互为同一份事实。

ADR 目录是第 20 轮才量到的空档：`active_docs()` 按 ``EXCLUDED_PARTS`` 把 ``adr/`` 整个
排除在文档-代码判据之外（历史语境不参与门禁，这个前提没错），于是**目录页自己**也一起
没人管了。实测到的三件事：

* ``docs/adr/README.md`` 的正文原本就是 ADR-001~005 五条决策本身（134 行），目录页
  没有索引可读——而这条缺陷在 ``docs/archive/plans/OPTIMIZATION_PLAN_v4.md:173``
  （2026-09-03）就写下过，之后没人行动；
* 编号有两处被重复声明：``ADR-007-010.md`` 用**一个复合编号**占住 007~010 四个号，
  与 ``ADR-006-010.md`` 里同号的四条逐条撞号；``ADR-013`` 更是两份文件同号，
  台账里"ADR-013 §11"这种带节号的引用只能靠人记住落在哪一份；
* ``ADR-009`` 的状态行写着 ``Accepted``，而它描述的 ``atst_native/`` 早在
  2026-09-23 的 ``git ls-files`` 里查无此路径——状态与事实分叉，且没有任何判据会响。

本轮补上索引（``docs/adr/README.md``）并把索引写成的规则钉在这里：**索引就是分母**。
新增一份 ADR 而漏加索引、索引指向不存在的文件、索引抄的状态与文件自己的状态行不符、
撞号没登记，都当场红。编号本身**不改**——这两个号被在写的台账按文件名整段引用，
改号会把活引用变成死指针；冲突登记在索引与文件里，由本文件的判据保证登记不脱落。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"

#: 一条决策的声明处：`# ADR-007: 标题` / `# ADR-007-010：标题`。
#: 文件级标题（`# ADR-001 ~ ADR-005：项目基线决策`）后面跟的是 ` ~ ` 而不是冒号，
#: 因此不会被当成声明——这正是它该有的样子：那行不声明任何一条决策。
_DECLARATION = re.compile(r"^#\s*ADR-([0-9]{3}(?:-[0-9]{3})*)\s*[:：]\s*(\S.*?)\s*$")

#: 状态行的四种既有写法：`**状态**: X`、`- 状态：X`、`- Status: X`、`Status: X`。
#: 要求冒号紧跟"状态"，所以 `- 状态机：draft → candidate → stable` 这种正文不算状态行。
_STATUS = re.compile(r"^(?:[-*]\s*)?(?:\*\*状态\*\*|状态|Status|status)\s*[:：]\s*(.+)$")

#: 索引表的一行：编号 | 标题 | 所在文件 | 状态 | 备注。
_ROW = re.compile(r"^\|\s*(ADR-[0-9]{3}(?:-[0-9]{3})*)\s*\|")
_LINK = re.compile(r"^\[`?([^`\]]+)`?\]\(([^)]+)\)$")

#: 状态关键词的合法取值。新增写法要连这里一起改，否则解析器会把"认不出"当成"没写"。
STATUS_KEYWORDS = frozenset(
    {
        "Accepted",
        "Proposed",
        "Deprecated",
        "Rejected",
        "Superseded",
        "Partially superseded",
        "Superseded in part",
        "已接受",
        "已取代",
        "已废弃",
    }
)

#: 撞号的登记判据词：索引行的备注里、承载冲突决策的文件里都要出现它。
_COLLISION_MARK = "撞号"
_FILE_WARNING = "编号警示"


@dataclass(frozen=True)
class Declaration:
    """一份文件里的一条决策（编号 + 标题 + 状态行原文）。"""

    number: str
    file: str
    title: str
    status: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.number, self.file)


@dataclass(frozen=True)
class IndexRow:
    """索引表里的一行；``file`` 已把「同上」展开成真实文件名。"""

    number: str
    file: str
    title: str
    status: str
    note: str
    link_text: str
    link_target: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.number, self.file)


def adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("*.md") if p.name != INDEX.name)


def status_keyword(status: str | None) -> str | None:
    """状态行开头的关键词：``Superseded（第 20 轮修正……）`` -> ``Superseded``。"""

    if status is None:
        return None
    return re.split(r"[（(—–]| - ", status, maxsplit=1)[0].strip()


def declarations() -> list[Declaration]:
    out: list[Declaration] = []
    for path in adr_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            found = _DECLARATION.match(line)
            if not found:
                continue
            status: str | None = None
            for following in lines[index + 1 :]:
                if _DECLARATION.match(following):
                    break
                hit = _STATUS.match(following.strip())
                if hit:
                    status = hit.group(1).strip()
                    break
            out.append(
                Declaration(
                    number=f"ADR-{found.group(1)}",
                    file=path.name,
                    title=found.group(2),
                    status=status,
                )
            )
    return out


def index_rows() -> list[IndexRow]:
    out: list[IndexRow] = []
    previous_file = ""
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not _ROW.match(line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"索引表不是五列形状（规则见该页「编号规则」一节）：{cells}"
        link = _LINK.match(cells[2])
        if link:
            target = urllib.parse.unquote(link.group(2))
            previous_file = Path(target).name
        else:
            #: 「同上」是索引里唯一一种不写链接的写法，它继承上一行的文件。
            assert cells[2] == "同上", f"所在文件一格既不是链接也不是「同上」：{cells[2]!r}"
        out.append(
            IndexRow(
                number=cells[0],
                file=previous_file,
                title=cells[1],
                status=cells[3],
                note=cells[4],
                link_text=link.group(1) if link else "",
                link_target=Path(urllib.parse.unquote(link.group(2))).name if link else "",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 判据本体：全部返回"人话清单"，好让正控能在内存里改一份数据再量一次
# --------------------------------------------------------------------------- #
def pair_findings(decls: list[Declaration], rows: list[IndexRow]) -> list[str]:
    """声明处与索引行必须是同一份 (编号, 文件) 集合，两个方向都管。"""

    declared, indexed = {d.key for d in decls}, {r.key for r in rows}
    out: list[str] = []
    out += [f"{f} 里的 {n} 没有索引行" for n, f in sorted(declared - indexed)]
    out += [f"索引里的 {n}（{f}）在磁盘上没有这条声明" for n, f in sorted(indexed - declared)]
    return out


def link_findings(rows: list[IndexRow]) -> list[str]:
    """链接目标必须存在，且链接文字就是那个文件名。"""

    out: list[str] = []
    for row in rows:
        if not row.link_target:
            continue
        target = ADR_DIR / row.link_target
        if not target.is_file():
            out.append(f"{row.number} 的索引链接指向不存在的 docs/adr/{row.link_target}")
        elif row.link_text != target.name:
            out.append(
                f"{row.number} 的索引链接文字 `{row.link_text}` 与被链文件 `{target.name}` 不同名"
            )
    return out


def status_findings(decls: list[Declaration], rows: list[IndexRow]) -> list[str]:
    """索引抄的状态必须以文件自己的状态行开头——ADR-009 那格就是被这条抓出来的。"""

    by_key = {d.key: d for d in decls}
    out: list[str] = []
    for row in rows:
        declaration = by_key.get(row.key)
        if declaration is None:
            continue
        if declaration.status is None:
            out.append(f"{row.file} 里的 {row.number} 没有状态行")
            continue
        keyword = status_keyword(declaration.status)
        if keyword not in STATUS_KEYWORDS:
            out.append(
                f"{row.file}::{row.number} 的状态关键词 `{keyword}` 不在已知写法里，"
                "本判据的解析器该改了"
            )
            continue
        if not row.status.startswith(keyword):
            out.append(
                f"索引把 {row.number}（{row.file}）写成 `{row.status}`，"
                f"文件自己的状态行是 `{keyword}`"
            )
    return out


def title_findings(decls: list[Declaration], rows: list[IndexRow]) -> list[str]:
    """索引标题必须是正文标题的开头（允许省略括号里的英文注解）。"""

    by_key = {d.key: d for d in decls}
    return [
        f"索引把 {row.number} 写成 `{row.title}`，文件里的标题是 `{by_key[row.key].title}`"
        for row in rows
        if row.key in by_key and not by_key[row.key].title.startswith(row.title)
    ]


def covered(number: str) -> set[int]:
    """`ADR-007` -> {7}；复合编号 `ADR-007-010` 占住 7~10 四个号。"""

    parts = [int(part) for part in number.removeprefix("ADR-").split("-")]
    return set(range(parts[0], parts[-1] + 1)) if len(parts) > 1 else {parts[0]}


def ambiguous_keys(decls: list[Declaration]) -> set[tuple[str, str]]:
    """与**别处**共享某个号的声明——同号重复、或复合编号覆盖了单号。"""

    keys = {d.key for d in decls}
    out: set[tuple[str, str]] = set()
    for left, right in ((d1, d2) for d1 in decls for d2 in decls if d1.key != d2.key):
        if covered(left.number) & covered(right.number):
            out |= {left.key, right.key}
    return out & keys


def collision_findings(decls: list[Declaration], rows: list[IndexRow]) -> list[str]:
    """撞号不许静默：索引行与承载文件两边都要登记。"""

    groups: dict[str, list[Declaration]] = {}
    for declaration in decls:
        groups.setdefault(declaration.number, []).append(declaration)
    out = [
        f"{number} 被 {len(mates)} 份文件声明，超过登记上限 2：{sorted(m.file for m in mates)}"
        for number, mates in groups.items()
        if len(mates) > 2
    ]
    ambiguous = ambiguous_keys(decls)
    by_key = {r.key: r for r in rows}
    out += [
        f"{number}（{file}）与别处撞号，索引行的备注里没写「{_COLLISION_MARK}」"
        for (number, file) in sorted(ambiguous)
        if _COLLISION_MARK not in by_key.get((number, file), rows[0]).note
    ]
    warned = {path.name for path in adr_files() if _FILE_WARNING in path.read_text("utf-8")}
    carrying = {file for _number, file in ambiguous} - warned
    out += [f"{file} 承载撞号决策，但文件里没有「{_FILE_WARNING}」行" for file in sorted(carrying)]
    return out


def next_number_findings(decls: list[Declaration], text: str) -> list[str]:
    """索引里「当前最大为 NNN，下一个是 NNN」这句必须是现算得出来的。"""

    claim = re.search(r"当前最大为\s*(\d{3})[，,]\s*下一个是\s*(\d{3})", text)
    if claim is None:
        return ["索引不再声明「当前最大为 / 下一个是」，本节判据失去对象"]
    plain = [int(d.number.removeprefix("ADR-")) for d in decls if "-" not in d.number[4:]]
    maximum = max(plain)
    out: list[str] = []
    if int(claim.group(1)) != maximum:
        out.append(f"索引说目录里最大是 {claim.group(1)}，实际声明到的最大号是 {maximum:03d}")
    if int(claim.group(2)) != maximum + 1:
        out.append(f"索引说下一个未占用的号是 {claim.group(2)}，按实际最大号应为 {maximum + 1:03d}")
    return out


# --------------------------------------------------------------------------- #
# 判据落地
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def adr() -> tuple[list[Declaration], list[IndexRow]]:
    return declarations(), index_rows()


def test_index_covers_every_declaration_and_nothing_else(adr) -> None:
    decls, rows = adr
    assert not pair_findings(decls, rows), "\n".join(pair_findings(decls, rows))


def test_index_links_land_on_files_that_exist(adr) -> None:
    _, rows = adr
    assert not link_findings(rows), "\n".join(link_findings(rows))


def test_index_status_matches_each_file_own_status_line(adr) -> None:
    decls, rows = adr
    assert not status_findings(decls, rows), "\n".join(status_findings(decls, rows))


def test_index_titles_are_the_declared_titles(adr) -> None:
    decls, rows = adr
    assert not title_findings(decls, rows), "\n".join(title_findings(decls, rows))


def test_numbering_collisions_are_registered_in_both_places(adr) -> None:
    decls, rows = adr
    assert not collision_findings(decls, rows), "\n".join(collision_findings(decls, rows))


def test_the_next_free_number_is_the_real_one(adr) -> None:
    decls, _ = adr
    findings = next_number_findings(decls, INDEX.read_text(encoding="utf-8"))
    assert not findings, "\n".join(findings)


def test_no_adr_file_is_outside_the_index(adr) -> None:
    """目录里每一份文件都得声明至少一条决策，也不能声明完却不被索引。"""

    decls, _ = adr
    silent = [path.name for path in adr_files() if not any(d.file == path.name for d in decls)]
    assert not silent, (
        f"这些文件一条决策都没声明（索引按 (编号, 文件) 配对，它们因此隐身）：{silent}"
    )


# --------------------------------------------------------------------------- #
# 防盲与正控
# --------------------------------------------------------------------------- #
def test_the_adr_ruler_is_not_blind(adr) -> None:
    """任何一张表空了都是尺子失效，不是"很干净"。"""

    decls, rows = adr
    assert len(decls) >= 18, f"只扫到 {len(decls)} 条 ADR 声明，声明处的形状变了"
    assert len(rows) >= 18, f"只读出 {len(rows)} 行索引，索引表的结构变了"
    assert len({d.file for d in decls}) == len(adr_files()), "有文件没被扫到"
    #: 状态行总数必须等于声明数：多了说明正文里有冒仿状态行的写法，少了说明有决策没写状态。
    markers = sum(
        1
        for path in adr_files()
        for line in path.read_text(encoding="utf-8").splitlines()
        if _STATUS.match(line.strip())
    )
    assert markers == len(decls), f"扫到 {markers} 行状态、{len(decls)} 条声明，两者已不相等"
    #: 复合编号与同号重复这两类冲突都得真的在目录里存在，否则撞号判据是空转。
    ambiguous = ambiguous_keys(decls)
    assert ("ADR-013", "ADR-013-provider-first-runtime-contract.md") in ambiguous, sorted(ambiguous)
    assert ("ADR-007-010", "ADR-007-010.md") in ambiguous, sorted(ambiguous)


def test_planted_defects_each_get_caught(adr) -> None:
    """正控：四类失效各造一次，判据必须只点出那一处。"""

    decls, rows = adr
    stale = [d for d in decls if d.number == "ADR-009"]
    assert len(stale) == 1
    #: ① 文件状态行被改回 Accepted，而索引仍写 Superseded。
    revived = [
        Declaration(d.number, d.file, d.title, "Accepted" if d.number == "ADR-009" else d.status)
        for d in decls
    ]
    hits = status_findings(revived, rows)
    assert len(hits) == 1 and "ADR-009" in hits[0], f"状态尺子对改回 Accepted 无感：{hits}"
    #: ② 新增一份没进索引的决策。
    orphan = [
        *decls,
        Declaration(
            "ADR-017", "ADR-016-config-surface-covers-execution-only.md", "占位", "Accepted"
        ),
    ]
    hits = pair_findings(orphan, rows)
    assert len(hits) == 1 and "ADR-017" in hits[0], f"漏加索引不会被拦下：{hits}"
    #: ③ 索引链接指向一个不存在的文件。
    broken = [
        IndexRow(
            r.number,
            "ADR-999-gone.md",
            r.title,
            r.status,
            r.note,
            "ADR-999-gone.md",
            "ADR-999-gone.md",
        )
        if r.number == "ADR-014"
        else r
        for r in rows
    ]
    hits = link_findings(broken)
    assert len(hits) == 1 and "ADR-014" in hits[0], f"死链不会被拦下：{hits}"
    #: ④ 把复合编号的撞号登记从备注里抹掉——它必须重新变红。
    unmarked = [
        IndexRow(r.number, r.file, r.title, r.status, "无", r.link_text, r.link_target)
        if r.number == "ADR-007-010"
        else r
        for r in rows
    ]
    hits = collision_findings(decls, unmarked)
    assert len(hits) == 1 and "ADR-007-010" in hits[0], f"撞号登记脱落不会被发现：{hits}"
    #: 反证：真实数据在这四条尺子下都是干净的。
    assert pair_findings(decls, rows) == []
    assert status_findings(decls, rows) == []
    assert link_findings(rows) == []
    assert collision_findings(decls, rows) == []
