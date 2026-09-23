"""证据指针门禁：台账自称的证据规模要现量，收走的树要就地写明。

第 20 轮"删除历史无效文件"这一问，普查量到的第一件事不是"该删哪些"，而是**删不动**：
本台账按名字引用了 45 棵 ``wt_*`` 工作树（本轮实测 212 处指针），全仓活文档合起来引用 67 棵
（``scratch_v18b20/deletable_trees.log``）——1.77 GB 的本机工作树因此是**证据存储**而不是垃圾，
它记的是取证现场。

于是"可删"要两条判据同时成立：① 树里每个非噪声文件的 blob 都在 ``git rev-list --objects --all``
里可达（本轮实测全历史可达对象 9323 个）；② 没有任何活文档按名字引用它。只有 4 棵通过，
本轮删了 2 棵，另 2 棵分别被 git 自己的"树里有未提交改动"守卫和"归并行会话所有"拦下。

普查同时抓到 **6 处指针指向已经不存在的树**（``wt_v18b16head/ship/step``、``wt_v18b17step``、
``wt_v18b18step``、``wt_v18review``）。写台账的人当时都留了提交锚（`616d065` / `dbe7652` /
`3dd14a3` / `2f1ceba` / `2ae022b`），**提交走得到任何一台机器，工作树走不到**——本轮把这 6 处
就地改写成"已回收 + 留存锚"，并把这条口径写成两把尺子：

① 台账在 §2 声明自己引用了几棵树、几处指针，判据现扫对账（声明即分母，改指针必须改声明）；
② 指向本机已不存在的树的指针，必须在同一逻辑块里写明「已回收」。这条只在**持有证据树的机器**
   上有意义：磁盘上一棵树都没有时整条跳过（全新克隆与 CI 就是这种环境），跳过条件写成
   "零棵树"而不是"看起来像 CI"——G14 那一族"钉在本地现场的门禁"就是把条件写反才红的。

判据对象限本会话所有的 ``REFACTOR_PLAN_V18_RESTRUCTURE.md``：并行会话的台账里另有 16 处死指针
（实测见 §29），按"不改他人台账"的纪律登记为 G19，不在这里替别人裁决。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.architecture.test_doc_code_consistency import logical_blocks

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "REFACTOR_PLAN_V18_RESTRUCTURE.md"

#: 工作树名。`wt_` 之后允许下划线分段（`wt_v18b16head`、`wt_s40base`）。
#: 尾部的负向预查是给普查脚本自己的日志名留的：`wt_census.log` 长得像树名，但它是一行
#: 输出文件而不是取证现场——把它算进"证据规模"等于让判据数错自己的账。
_TREE = re.compile(r"\bwt_[0-9A-Za-z]+(?:_[0-9A-Za-z]+)*\b(?!\.(?:log|py|md|txt|sh|json|xml))")
#: 与 `test_doc_code_consistency._RETIRED_MARKERS` 同族，这里只收指针语境下成立的那几个。
_RECYCLED_MARKERS = ("已回收", "已删除", "已清理", "已不存在", "本机已无")
#: 台账 §2 里那句自我声明；两个数字都是判据的账。
_DECLARATION = re.compile(r"引用\s*(\d+)\s*棵\s*`wt_\*`\s*工作树，共\s*(\d+)\s*处指针")


def evidence_trees() -> set[str]:
    """本机现存的证据树（`wt_*` 目录）。"""

    return {path.name for path in ROOT.glob("wt_*") if path.is_dir()}


def citation_counts(text: str) -> tuple[int, int]:
    """现扫台账全文： ``(被引用的树数, 指针出现次数)``。"""

    names = _TREE.findall(text)
    return len(set(names)), len(names)


def pointer_blocks(text: str) -> list[tuple[str, list[str]]]:
    """``(逻辑块, 块内树名)``——只含带指针的块；块粒度与斜杠死路径判据同源。"""

    return [
        (block, sorted(set(_TREE.findall(block))))
        for block in logical_blocks(text)
        if _TREE.search(block)
    ]


def declaration_findings(text: str) -> list[str]:
    """判据 ①：台账声明的证据规模必须等于现扫的那份。"""

    claim = _DECLARATION.search(text)
    if claim is None:
        return ["§2 不再声明「引用 N 棵 `wt_*` 工作树，共 M 处指针」，本判据失去对象"]
    trees, pointers = citation_counts(text)
    out: list[str] = []
    if int(claim.group(1)) != trees:
        out.append(f"台账声明引用 {claim.group(1)} 棵树，现扫是 {trees} 棵")
    if int(claim.group(2)) != pointers:
        out.append(f"台账声明 {claim.group(2)} 处指针，现扫是 {pointers} 处")
    return out


def dead_pointer_findings(
    blocks: list[tuple[str, list[str]]], trees_on_disk: set[str]
) -> list[str]:
    """判据 ②：指向已消失的树的指针必须写明「已回收」。"""

    return [
        f"{tree} 已不在磁盘上，但所在块仍以现时语气引用（缺 {'/'.join(_RECYCLED_MARKERS)} 之一）"
        for block, trees in blocks
        for tree in trees
        if tree not in trees_on_disk and not any(marker in block for marker in _RECYCLED_MARKERS)
    ]


@pytest.fixture(scope="module")
def ledger() -> str:
    return LEDGER.read_text(encoding="utf-8")


_no_trees = not evidence_trees()


def test_the_ledger_declares_the_size_of_its_evidence_store(ledger: str) -> None:
    findings = declaration_findings(ledger)
    assert not findings, "\n".join(findings)


@pytest.mark.skipif(_no_trees, reason="本机一棵证据树都没有：死指针判据没有对象可量")
def test_pointers_at_recycled_trees_say_so(ledger: str) -> None:
    findings = dead_pointer_findings(pointer_blocks(ledger), evidence_trees())
    assert not findings, "\n".join(findings)


def test_the_pointer_ruler_is_not_blind(ledger: str) -> None:
    """防盲：一块都没扫到就是尺子失效，不是"台账很干净"。"""

    blocks = pointer_blocks(ledger)
    trees = {tree for _block, names in blocks for tree in names}
    assert len(blocks) >= 60, f"台账里只读出 {len(blocks)} 个含指针的块"
    assert len(trees) >= 30, f"台账里只认得 {len(trees)} 棵树名，指针的形状变了"
    trees_on_disk = evidence_trees()
    assert trees_on_disk or _no_trees, "本判据的跳过条件自身失效：磁盘上有树却没被扫到"


def test_planted_defects_each_get_caught(ledger: str) -> None:
    """正控：两类失效各造一次，判据必须只点出那一处。"""

    #: ① 悄悄多加一处指针（不改声明）。
    inflated = f"{ledger}\n本轮另跑过 `wt_v18b99planted/fulltest.log`。\n"
    trees, pointers = citation_counts(inflated)
    findings = declaration_findings(inflated)
    assert len(findings) == 2, f"多出来的指针没有被两格账同时抓到：{findings}"
    assert any(f"现扫是 {trees} 棵" in line for line in findings), findings
    assert any(f"现扫是 {pointers} 处" in line for line in findings), findings
    assert declaration_findings(ledger) == []
    #: ② 把一棵磁盘上确无的树写成现时语气。
    on_disk = evidence_trees()
    phantom = next(
        (
            tree
            for tree in sorted({t for _b, names in pointer_blocks(ledger) for t in names})
            if tree not in on_disk
        ),
        "",
    )
    assert phantom, "台账里已经没有死指针，本正控需要重新造现场"
    findings = dead_pointer_findings([(f"候选树 `{phantom}` 的日志见 §七", [phantom])], on_disk)
    assert len(findings) == 1 and phantom in findings[0], findings
    #: 同一段落一旦写明「已回收」，豁免必须生效（否则豁免词形同虚设）。
    assert dead_pointer_findings([(f"候选树 `{phantom}`（已回收）", [phantom])], on_disk) == [], (
        "豁免词不生效，判据 ② 会把登记过的回收也报成违约"
    )
