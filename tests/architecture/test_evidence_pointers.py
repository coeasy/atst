"""证据指针门禁：台账自称的证据规模要现量，收走的树要就地写明。

第 20 轮"删除历史无效文件"这一问，普查量到的第一件事不是"该删哪些"，而是**删不动**：
本台账当时按名字引用了 45 棵 ``wt_*`` 工作树（第 20 轮实测 212 处指针；第 21 轮的两遍普查把它
推到 47 棵 / 216 处，现值以 §2 那句声明为准），全仓活文档合起来引用 67 棵
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
   上有意义：跳过条件写成"这棵树的仓内那一支 ``wt_*`` 证据目录是空的"（全新克隆、CI 与
   **候选工作树**都是这种形状）。第 21 轮写的是"磁盘上一棵都没有"，第 22 轮改成看仓内——
   理由见 :func:`holds_evidence_store`：判据该在哪种环境里运行，不能由隔壁目录活没活着决定。
③ 相对 HEAD **新增**的树名，除了"本机存在"只剩一条出路：每一处引用格里写明「已回收」**并且**
   给出一个解得开的提交锚（:func:`recycled_registration`）。② 不查锚，③ 查——新指针登记的
   代价比历史指针高，这是第 25 轮补的形状，理由写在该判据的 docstring 里。

判据对象限本会话所有的 ``REFACTOR_PLAN_V18_RESTRUCTURE.md``：并行会话的台账里另有 16 处死指针
（实测见 §29），按"不改他人台账"的纪律登记为 G19，不在这里替别人裁决。
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

import pytest

from tests.architecture.test_doc_code_consistency import logical_blocks

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs" / "REFACTOR_PLAN_V18_RESTRUCTURE.md"

#: 工作树名。`wt_` 之后允许下划线分段（`wt_v18b16head`、`wt_s40base`）。
#: 尾部的负向预查是给普查脚本自己的日志名留的：`wt_census.log` 长得像树名，但它是一行
#: 输出文件而不是取证现场——把它算进"证据规模"等于让判据数错自己的账。
#:
#: 前缀 `atst_` 是第 21 轮补的第二种形状。这一轮起候选树落在**仓库外面**
#: （`P:/github_public/atst_wt_v18b21step`），目录名因此带上 `atst_` 前缀；而 `\bwt_` 在
#: `x_w` 之间不成立，旧正则**一处都数不到它**——台账写满这种指针也不会红，判据 ② 也就永远
#: 追不到它们。归一化（剥掉前缀）由 `_tree_names` 一处负责，磁盘侧与台账侧走同一条路，
#: 否则"存在"会被读成"已消失"。
_TREE = re.compile(
    r"\b(?:atst_)?wt_[0-9A-Za-z]+(?:_[0-9A-Za-z]+)*\b(?!\.(?:log|py|md|txt|sh|json|xml))"
)


def _tree_names(text: str) -> list[str]:
    """按形状扫出树名，并把仓库外那种 `atst_wt_*` 归一化成 `wt_*`。"""

    return [name.removeprefix("atst_") for name in _TREE.findall(text)]


#: 仓外**绝对路径**指针的第一段目录名（`P:/github_public/<目录>`）。第 26 轮补的形状：
#: `_TREE` 只认 `wt_` 开头的名字，而台账还把三类现场写成绝对路径——发布用的干净导出树、
#: runner 所在的工作目录、门禁日志目录。这一族对旧尺子完全隐形（实测：台账里 4 个这样的
#: 名字，3 个已从磁盘消失，判据 ②/③ 一处都抓不到）。只取第一段，因为判据的对象是"那个
#: 现场目录还在不在"，不是目录里某个文件。
_ABS_DIR = re.compile(r"[A-Za-z]:/github_public/(?!\.)([0-9A-Za-z][0-9A-Za-z_.-]*)")


def _dir_names(text: str) -> list[str]:
    return _ABS_DIR.findall(text)


#: 与 `test_doc_code_consistency._RETIRED_MARKERS` 同族，这里只收指针语境下成立的那几个。
_RECYCLED_MARKERS = ("已回收", "已删除", "已清理", "已不存在", "本机已无")
#: 提交锚。G18 的口径从来是「已回收 **+ 锚**」，锚必须是一条走得通的引用，不是一个词。
_ANCHOR = re.compile(r"\b[0-9a-f]{7,40}\b")
#: 台账 §2 里那句自我声明；两个数字都是判据的账。
_DECLARATION = re.compile(r"引用\s*(\d+)\s*棵\s*`wt_\*`\s*工作树，共\s*(\d+)\s*处指针")


@cache
def _anchor_resolves(rev: str) -> bool:
    """这个短串在本仓库里解得开成一个提交吗（解不开 = 锚是假的，登记不算登记）。"""

    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


def recycled_registration(block: str) -> bool:
    """这一格里写了「已回收」，并且给了一个**解得开的**提交锚。"""

    return any(marker in block for marker in _RECYCLED_MARKERS) and any(
        _anchor_resolves(rev) for rev in _ANCHOR.findall(block)
    )


def _in_repo_store(root: Path) -> set[str]:
    """这棵树的**仓内**那一支证据目录（``wt_*``）。判据 ② 只在持有它的那棵树里运行。"""

    return {path.name for path in root.glob("wt_*") if path.is_dir()}


def _trees_under(root: Path) -> set[str]:
    """从 ``root`` 这棵树的位置看：本机现存的证据树叫什么。

    仓库内是 ``wt_*``，仓库外是 ``atst_wt_*``。仓库外那一支必须排掉**正在量的这棵树自己**：
    候选工作树就躺在证据目录的旁边，把它算成"本机持有证据"会让跳过条件失效——判据 ② 于是
    对着一个只有自己的磁盘，把 47 棵已回收的树全报成违约。量尺不能是自己的证据。
    """

    outside = {
        path.name.removeprefix("atst_")
        for path in root.parent.glob("atst_wt_*")
        if path.is_dir() and path.resolve() != root.resolve()
    }
    return _in_repo_store(root) | outside


def holds_evidence_store(root: Path = ROOT) -> bool:
    """本机是否持有这份台账的证据库——只看仓内那一支。

    第 22 轮把跳过条件从"一棵树都没有"改成这一条，因为上一版的代理条件量错了对象：
    仓外那些 ``atst_wt_*`` 是**别轮次的测量现场**，不是这份台账的证据存储。第 22 轮的
    候选树里，上一轮遗留的 ``atst_wt_v18b21step`` 还活着，于是"零棵树"不再成立，判据 ②
    在那棵树上把 47 棵已回收的树报成违约；而把那座遗留目录回收之后同一判据又自动变回跳过——
    这两次翻转都只发生在**改前**那个条件上。
    **读数随邻居的存在性漂移**正是 G20/G24 那一族自指要防的形状：一条判据该在什么环境里
    运行，只能由它自己的对象决定。
    """

    return bool(_in_repo_store(root))


def evidence_trees() -> set[str]:
    """本机现存的证据树（仓库内的 `wt_*`，与仓库外的 `atst_wt_*`）。"""

    return _trees_under(ROOT)


def citation_counts(text: str) -> tuple[int, int]:
    """现扫台账全文： ``(被引用的树数, 指针出现次数)``。"""

    names = _tree_names(text)
    return len(set(names)), len(names)


def pointer_blocks(text: str) -> list[tuple[str, list[str]]]:
    """``(逻辑块, 块内树名)``——只含带指针的块；块粒度与斜杠死路径判据同源。"""

    return [
        (block, sorted(set(_tree_names(block))))
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


def dirs_on_disk(root: Path = ROOT) -> set[str]:
    """本机现存的仓外现场目录（与被引用名字同一层：`root.parent`）。"""

    return {path.name for path in root.parent.iterdir() if path.is_dir()}


def dir_blocks(text: str) -> list[tuple[str, list[str]]]:
    """``(逻辑块, 块内绝对路径目录名)``——只含带这类指针的块。"""

    return [
        (block, sorted(set(_dir_names(block))))
        for block in logical_blocks(text)
        if _ABS_DIR.search(block)
    ]


def dead_dir_findings(blocks: list[tuple[str, list[str]]], on_disk: set[str]) -> list[str]:
    """同一族判据② 的另一半：指向已消失的**仓外现场目录**的指针必须写明「已回收」。"""

    return [
        f"{name} 已不在磁盘上，但所在块仍以现时语气引用绝对路径"
        f"（缺 {'/'.join(_RECYCLED_MARKERS)} 之一）"
        for block, names in blocks
        for name in names
        if name not in on_disk and not any(marker in block for marker in _RECYCLED_MARKERS)
    ]


@pytest.fixture(scope="module")
def ledger() -> str:
    return LEDGER.read_text(encoding="utf-8")


_no_trees = not holds_evidence_store()


def test_the_ledger_declares_the_size_of_its_evidence_store(ledger: str) -> None:
    findings = declaration_findings(ledger)
    assert not findings, "\n".join(findings)


@pytest.mark.skipif(_no_trees, reason="本机一棵证据树都没有：死指针判据没有对象可量")
def test_pointers_at_recycled_trees_say_so(ledger: str) -> None:
    findings = dead_pointer_findings(pointer_blocks(ledger), evidence_trees())
    assert not findings, "\n".join(findings)


@pytest.mark.skipif(_no_trees, reason="本机不持有证据库：仓外现场目录的账没有对象可对")
def test_pointers_at_recycled_out_of_repo_sites_say_so(ledger: str) -> None:
    """G42：仓外绝对路径指针（导出树 / runner 目录 / 日志目录）与工作树走同一条口径。

    跳过条件与判据 ② 用同一个 `holds_evidence_store()`：这类目录只存在于"本机持有这份台账
    的证据库"那种机器上，CI 与全新克隆里 `P:/github_public` 这一层根本不存在，让它运行就会
    把每一条绝对路径指针报成违约（G24 那一次假红的形状：扩大扫描范围必须同时复查跳过条件）。
    """

    findings = dead_dir_findings(dir_blocks(ledger), dirs_on_disk())
    assert not findings, "\n".join(findings)


def test_out_of_repo_dir_pointers_are_neither_blind_nor_self_exempting(tmp_path: Path) -> None:
    """正控：新补的这一族形状要认得、违约要抓到、写明回收要放行。"""

    #: 形状识别（不看磁盘——台账此刻完全可以只写工作树形状，而尺子对绝对路径已经失明）。
    both = _dir_names("P:/github_public/scratch_v18b99/gates.log 与 p:/github_public/site22")
    assert both == ["scratch_v18b99", "site22"], f"绝对路径目录名扫错：{both}"
    #: 磁盘上没有的名字，裸写必须抓到；同一块里写明回收必须放行。
    (tmp_path / "atst").mkdir()
    measuring = tmp_path / "atst"
    gone = "P:/github_public/site_missing22/run.log"
    present = "P:/github_public/atst"
    on_disk = dirs_on_disk(measuring)
    assert "atst" in on_disk
    findings = dead_dir_findings(dir_blocks(f"门禁日志在 `{gone}`。"), on_disk)
    assert len(findings) == 1 and "site_missing22" in findings[0], findings
    assert dead_dir_findings(dir_blocks(f"门禁日志在 `{gone}`（本机已回收）。"), on_disk) == []
    #: 现存的那条不该被报成违约，否则豁免词会逼着活现场也写"已回收"。
    assert dead_dir_findings(dir_blocks(f"被测树在 `{present}`。"), on_disk) == []


def test_the_pointer_ruler_is_not_blind(ledger: str) -> None:
    """防盲：一块都没扫到就是尺子失效，不是"台账很干净"。"""

    blocks = pointer_blocks(ledger)
    trees = {tree for _block, names in blocks for tree in names}
    assert len(blocks) >= 60, f"台账里只读出 {len(blocks)} 个含指针的块"
    assert len(trees) >= 30, f"台账里只认得 {len(trees)} 棵树名，指针的形状变了"
    #: 两种命名形状都得认得：仓库内 `wt_*` 与仓库外 `atst_wt_*`（第 21 轮起的候选树）。
    #: 这一条不看台账——台账此刻完全可以只写一种形状，而尺子对另一种已经失明。
    both = _tree_names("wt_v18b99inside 与 atst_wt_v18b99outside")
    assert both == ["wt_v18b99inside", "wt_v18b99outside"], f"前缀归一化失效：{both}"
    trees_on_disk = evidence_trees()
    assert trees_on_disk or _no_trees, "本判据的跳过条件自身失效：磁盘上有树却没被扫到"


def test_the_measuring_tree_is_not_its_own_evidence(tmp_path: Path) -> None:
    """正控：仓库外那条扫描支路不能把"正在量的这棵树"算成证据。

    第 21 轮补前缀时踩到的那一格：候选树 `atst_wt_*` 与证据目录同级，扫法不改就会在
    候选树里数出"本机持有 1 棵"，跳过条件从此不再成立，判据 ② 把 47 棵已回收的树全报成违约。
    """

    measuring = tmp_path / "atst_wt_measuring"
    (tmp_path / "atst_wt_evidence").mkdir()
    measuring.mkdir()
    (measuring / "wt_child").mkdir()
    #: 断言是集合等式，所以"排掉自己"退化成"仓库外那一支永远为空"也会在这里红。
    assert _trees_under(measuring) == {"wt_evidence", "wt_child"}, _trees_under(measuring)
    #: 只有一棵孤立的候选树时，跳过条件必须仍然成立（全新克隆/CI 就是这个形状）。
    lone = tmp_path / "lone" / "atst_wt_only"
    lone.mkdir(parents=True)
    assert _trees_under(lone) == set(), "候选树把自己当成了证据，跳过条件会被它顶穿"


def test_the_run_condition_is_not_decided_by_a_neighbour(tmp_path: Path) -> None:
    """正控：判据 ② 在不在本机运行，只看仓内那一支，隔壁轮次的候选树不许顶穿它。

    第 22 轮的实际现场：候选树里仓内 ``wt_*`` 为空、而上一轮遗留的 ``atst_wt_v18b21step``
    还在同一层目录里活着——旧跳过条件因此不成立，47 棵已回收的树被报成违约。
    """

    candidate = tmp_path / "atst_wt_round22"
    candidate.mkdir()
    (tmp_path / "atst_wt_round21").mkdir()
    #: 上一轮那棵遗留树确实被扫进"本机现存证据"（它存在就是存在）……
    assert _trees_under(candidate) == {"wt_round21"}
    #: ……但它不该决定这条判据运行与否：这棵树没有仓内证据库，② 没有对象可量。
    assert not holds_evidence_store(candidate), (
        "隔壁轮次的候选树顶穿了跳过条件，读数会随邻居的存在性漂移"
    )
    #: 反向退化：仓内有证据库时必须运行，否则 ② 会永远跳过而看起来像"全绿"。
    store = tmp_path / "checkout"
    (store / "wt_v18b1step").mkdir(parents=True)
    assert holds_evidence_store(store), "持有证据库的那棵树被跳过了，② 成了永不运行的判据"


def unlawful_new_pointers(text: str, added: set[str], on_disk: set[str]) -> set[str]:
    """新增树名里"既不现存、又没登记清楚"的那些。

    登记清楚 = **每一处**引用它的逻辑块都同时写着「已回收」与一个解得开的提交锚。只看带名字
    的那几格，所以隔壁格子里喊一万次"已回收"也救不了一处裸指针。
    """

    blocks = pointer_blocks(text)
    return {
        tree
        for tree in added - on_disk
        if not all(recycled_registration(block) for block, names in blocks if tree in names)
    }


def test_new_pointers_must_be_lawful_additions_not_pre_declared_exemptions(
    ledger: str,
) -> None:
    """正控：新增指针只有两条合法出路——那棵树**在盘上**，或者就地按「已回收 + 锚」登记。

    判据 ① 的形状是"声明 == 现扫"，所以只要声明跟着改数，多加指针不赔；真正拦住它的是
    这一格。拿 HEAD 版台账当基线（它自己必须自洽，否则下面的差集推理没有根据），要求：

    * 基线的声明 == 基线的现扫（尺子对"改前"也不瞎）；
    * 本轮新引入的每个树名要么**本机存在**，要么在**每一处**引用格里登记为已回收，且登记的锚
      解得开（:func:`recycled_registration`）——把一棵不在盘上的树写成新指针，不能被
      "我声明了"洗成合法；
    * 指针处数单调不减（这是棘轮，下一轮仍然成立，不靠硬编码本轮树名）。

    第二条原本是"必须本机存在"，没有豁免支路。第 25 轮量到它没有出路：台账文本写在**未提交**
    的工作树里、指针当时指的就是当时的候选树，那棵树随后被回收（`wt_v18b23step`，锚 `07477e8`），
    于是"新增"与"不在盘上"同时成立，而唯一被报错信息指明的出路（按「已回收」登记）恰好就是
    违约本身。判据的意图从来不是禁止登记过的回收，而是禁止**裸的**死指针，所以这一格改判成
    现在这样，并且比原来更严：新增指针要付"锚必须解得开"这一格，判据 ② 至今不查锚。
    四条形种各造一次（裸名／只写「已回收」不给锚／给一个解不开的锚／给真锚），只有最后一格
    必须放行。
    """

    before = subprocess.run(
        ["git", "show", "HEAD:docs/REFACTOR_PLAN_V18_RESTRUCTURE.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    assert declaration_findings(before) == [], "HEAD 版台账的声明与现扫不自洽，基线失去意义"
    #: ``_trees_under`` 故意把**正在量的这棵树**排掉（G24：量尺不能是自己的证据），所以在本轮
    #: 候选树里跑时，台账引用的那棵候选树恰好不在清单上——这一条要把它算回来，否则
    #: "新指针必须指得到现存现场"会在测量现场自己家里当场假红。
    on_disk = evidence_trees() | {ROOT.name.removeprefix("atst_")}
    added = set(_tree_names(ledger)) - set(_tree_names(before))
    offenders = unlawful_new_pointers(ledger, added, on_disk)
    assert not offenders, (
        f"本轮新引用的树里有不在盘上、又没登记回收的：{sorted(offenders)}"
        "——新指针只能指向现存的测量现场，回收过的要在每一处引用格里写明「已回收 + 解得开的锚」，"
        "不能靠改声明通过"
    )
    assert citation_counts(ledger)[1] >= citation_counts(before)[1], (
        "指针处数比 HEAD 少：删除指针要走 G18 的单调收缩口径，不能顺手抹掉"
    )
    assert declaration_findings(ledger) == []
    #: 正控：种一棵不在盘上的新树名，上面那条必须抓到它。
    phantom_name = "wt_v18b99planted"
    planted = set(_tree_names(f"{ledger}\n本轮另跑过 `{phantom_name}`。\n")) - set(
        _tree_names(before)
    )
    assert phantom_name in planted and planted - on_disk, (
        "种下去的死指针没被认成新增——本判据对『先声明后违约』是瞎的"
    )
    #: 四条形种：只有"写明回收 + 锚解得开"这一格放行，其余三格必须当场抓到。
    anchor = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    shapes = {
        "裸名": f"本轮另跑过 `{phantom_name}`。",
        "只写已回收": f"本轮另跑过 `{phantom_name}`（已回收）。",
        "锚解不开": f"本轮另跑过 `{phantom_name}`（已回收，锚 = 提交 `ffffffffffffffff`）。",
        "真锚": f"本轮另跑过 `{phantom_name}`（已回收，锚 = 提交 `{anchor}`）。",
    }
    for shape, line in shapes.items():
        caught = unlawful_new_pointers(ledger + "\n" + line, planted, on_disk)
        expect_caught = shape != "真锚"
        assert (phantom_name in caught) is expect_caught, f"形种「{shape}」判错：{sorted(caught)}"


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
