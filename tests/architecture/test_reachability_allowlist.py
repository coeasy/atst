# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""可达性白名单自身的契约（审计 F-22）。

豁免一条模块等于宣布"它不可达是预期的"。因此记录本身必须是被门禁的对象：
死记录（模块已不存在）与过期记录（模块已可达）都会把将来的真断链读成绿。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _scanner():
    spec = importlib.util.spec_from_file_location(
        "audit_reachability", ROOT / "scripts" / "audit_reachability.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ar = _scanner()


def _write(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "_reach_allow.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


#: 够长且合格的理由样本——带一个真实存在的指针，各缺陷用例只在"模块对不对/是否重复"
#: 这一维度上失败，不会被 `[no-pointer]` 顺带判红。
LONG_REASON = (
    "公共 API：由用户显式 import，内核不 import 属预期，"
    "tests/architecture/test_reachability_allowlist.py 逐条覆盖其语义与边界"
)


# --------------------------------------------------------------------------- #
# 真实清单必须干净
# --------------------------------------------------------------------------- #
def test_real_allowlist_records_are_well_formed() -> None:
    records, defects = ar._load_allow()
    assert records, "白名单不应为空"
    assert defects == []


def test_real_allowlist_has_no_dead_records() -> None:
    modules, _ = ar._collect_modules()
    records, _ = ar._load_allow()
    defects = ar._allowlist_defects(records, set(modules), set())
    assert not [d for d in defects if d.startswith("[dead]")]


def test_strict_gate_passes_on_current_tree() -> None:
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", ["audit_reachability.py", "--strict"])
    try:
        assert ar.main() == 0
    finally:
        monkey.undo()


# --------------------------------------------------------------------------- #
# 进程入口不靠豁免
# --------------------------------------------------------------------------- #
def test_process_entrypoints_are_seeded_not_exempted() -> None:
    modules, _ = ar._collect_modules()
    entry = ar._entrypoints(modules)
    assert {"atst.cli", "atst.__main__", "atst.tools.spec_audit"} <= entry
    assert "atst.runtime.kernel" not in entry
    records, _ = ar._load_allow()
    assert not ({"atst.cli", "atst.__main__"} & set(records))


# --------------------------------------------------------------------------- #
# 六类记录缺陷各自都被抓住
# --------------------------------------------------------------------------- #
def test_thin_reason_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, "atst.batch  # 太短")
    records, defects = ar._load_allow(path)
    assert list(records) == ["atst.batch"]
    assert len(defects) == 1 and defects[0].startswith("[thin]")


def test_duplicate_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"atst.batch  # {LONG_REASON}", f"atst.batch  # {LONG_REASON}")
    _, defects = ar._load_allow(path)
    assert [d.split("]")[0] for d in defects] == ["[dup"]


def test_dead_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"atst.nope  # {LONG_REASON}")
    records, parse_defects = ar._load_allow(path)
    assert parse_defects == []
    defects = ar._allowlist_defects(records, {"atst.batch"}, set())
    assert [d.split("]")[0] for d in defects] == ["[dead"]


def test_stale_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"atst.batch  # {LONG_REASON}")
    records, _ = ar._load_allow(path)
    defects = ar._allowlist_defects(records, {"atst.batch"}, {"atst.batch"})
    assert [d.split("]")[0] for d in defects] == ["[stale"]


# --------------------------------------------------------------------------- #
# "谁消费它"必须是可核验的指针，不是自由文本
# --------------------------------------------------------------------------- #
def test_reason_pointing_at_a_removed_path_is_a_defect(tmp_path: Path) -> None:
    """豁免理由引用的目录被改名/删除后，这条证据就已经不成立，必须当场判红。"""
    path = _write(
        tmp_path,
        "atst.batch  # 由 tests/this_directory_was_renamed_long_ago/ 与它的测试消费，"
        "内核不 import 属预期，理由本身够长不会被 thin 抢先",
    )
    _, defects = ar._load_allow(path)
    assert [d.split("]")[0] for d in defects] == ["[dead-pointer"]


def test_reason_without_any_verifiable_pointer_is_a_defect(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "atst.batch  # 这是一段说得很长很圆、却一个文件都举不出来的自由文本理由，"
        "读的人无法反驳，因此也无法信任",
    )
    _, defects = ar._load_allow(path)
    assert [d.split("]")[0] for d in defects] == ["[no-pointer"]


def test_the_pointer_ruler_itself_sees_the_real_records() -> None:
    """自检：真实清单的每条理由都要拿出至少一个活指针，否则上面两条判据可能在空转。"""
    records, _ = ar._load_allow()
    assert records
    empty = [m for m, r in records.items() if not ar._consumer_pointers(r)[0]]
    assert not empty, f"这些豁免记录拿不出存在的消费方路径：{empty}"


# --------------------------------------------------------------------------- #
# 指针必须"有内容"：点名的文件要真的触达被豁免的模块
# --------------------------------------------------------------------------- #
def test_real_allowlist_pointers_reach_the_modules_they_excuse() -> None:
    """真实清单的每条理由都要逐格复核：点名的 .py 文件确实 import（或按全名提及）该模块。"""
    modules, packages = ar._collect_modules()
    records, _ = ar._load_allow()
    graph = ar._build_graph(modules, packages, set(records))
    assert ar._pointer_defects(records, modules, graph) == []


#: 三条指针用例共用的理由正文：只有指针指向的文件不同，判定差异不能来自理由长度。
REASON_TAIL = (
    " 消费它，内核不 import 属有意设计，理由本身足够长，不会被 thin 或 no-pointer 抢先判红"
)


def test_a_pointer_at_an_unrelated_file_is_a_defect(tmp_path: Path) -> None:
    """ "某某测试覆盖它"必须真的覆盖它——引用的文件与模块无关即判红。

    records 直接构造而不走 :func:`_load_allow`：后者对指针只做"文件在不在磁盘"
    （``[dead-pointer]``，另有专测），而这一格要的正是"存在但无关"的形状。
    """
    unrelated = tmp_path / "tests" / "unrelated.py"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("import atst.charset\n", encoding="utf-8")
    found = ar._pointer_defects(
        {"atst.batch": f"由 tests/unrelated.py{REASON_TAIL}"}, {}, {}, root=tmp_path
    )
    assert [d.split("]")[0] for d in found] == ["[weak-pointer"], found


def test_a_pointer_that_really_touches_the_module_is_accepted(tmp_path: Path) -> None:
    """正控的正面：同一形状，只要文件真的 import 到该模块就不该报。"""
    consumer = tmp_path / "tests" / "real.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text("from atst.batch import __version__\n", encoding="utf-8")
    records = {"atst.batch": f"由 tests/real.py{REASON_TAIL}"}
    assert ar._pointer_defects(records, {}, {}, root=tmp_path) == []


def test_a_pointer_via_a_parent_package_export_is_accepted(tmp_path: Path) -> None:
    """``from atst.trade import X`` 也算触达 ``atst.trade.client``——只要父包真的导出它。"""
    consumer = tmp_path / "tests" / "pkg.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text("from atst.trade import TradeClient\n", encoding="utf-8")
    graph = {"atst.trade": {"atst.trade.client"}, "atst.trade.client": set()}
    records = {"atst.trade.client": f"由 tests/pkg.py{REASON_TAIL}"}
    assert ar._pointer_defects(records, {}, graph, root=tmp_path) == []


# --------------------------------------------------------------------------- #
# 种子表自身
# --------------------------------------------------------------------------- #
def test_every_seed_names_a_real_module() -> None:
    """SEEDS 里的名字必须都存在：BFS 的 ``if s in modules`` 会静默丢弃改名掉的种子。"""
    modules, _ = ar._collect_modules()
    assert ar._seed_defects(set(modules)) == []


def test_a_stale_seed_is_a_defect() -> None:
    defects = ar._seed_defects({"atst"}, seeds={"atst", "atst.integration.http_server"})
    assert [d.split("]")[0] for d in defects] == ["[dead-seed"]
    assert "atst.integration.http_server" in defects[0]


# --------------------------------------------------------------------------- #
# 理由点名的**符号**必须真在该模块里（V18 第 6 轮）
# --------------------------------------------------------------------------- #


def _fake_module(tmp_path: Path, stem: str, source: str) -> tuple[dict[str, Path], str]:
    """造一个 ``atst/<stem>.py`` 的假模块，返回 ``(modules 映射, 点号模块名)``。"""
    module = tmp_path / "atst" / f"{stem}.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    dotted = f"atst.{stem}"
    return {dotted: module}, dotted


#: 只替换符号名，理由其余部分逐字相同：判定差异不许来自正文长度。
CLAIM_TAIL = " 是本模块的公共入口，内核不 import 属有意设计，理由本身足够长"


def test_real_allowlist_symbol_claims_resolve() -> None:
    """真实清单里每一格反引号点名的 API 名，都要在被豁免模块的静态命名空间里。"""
    modules, _ = ar._collect_modules()
    records, _ = ar._load_allow()
    claims = {
        m: [
            c
            for c in sorted(set(ar._SYMBOL_CLAIM.findall(r)))
            if "/" not in c and "." not in c and not c.endswith((".py", ".md"))
        ]
        for m, r in records.items()
    }
    total = sum(len(v) for v in claims.values())
    assert total >= 40, f"全清单只点名了 {total} 格符号，证据面太薄，判据等于没上"
    assert ar._claim_defects(records, modules) == []


def test_a_claimed_symbol_that_the_module_does_not_have_is_a_defect(tmp_path: Path) -> None:
    """写着 `CapabilityContract` 而模块里其实叫别的名字——按图索骥的人一个也找不到。"""
    modules, dotted = _fake_module(tmp_path, "phantom", "RealName = 1\n\n\nother = 2\n")
    found = ar._claim_defects(
        {dotted: f"`RealName`/`PhantomName`{CLAIM_TAIL}"}, modules, min_claims=0
    )
    assert [d.split("]")[0] for d in found] == ["[dead-claim"], found
    assert "PhantomName" in found[0] and "`RealName`" not in found[0]


def test_a_reexported_name_counts_as_part_of_the_namespace(tmp_path: Path) -> None:
    """正控的正面：`from .impl import Thing` 再导出与自身定义同等有效——真实清单靠这个吃饭。"""
    modules, dotted = _fake_module(
        tmp_path,
        "reexport",
        "from .impl import ViaImport  # noqa: F401\n\n\nclass Defined:\n    pass\n",
    )
    records = {dotted: f"`ViaImport`/`Defined`{CLAIM_TAIL}"}
    assert ar._claim_defects(records, modules, min_claims=0) == []


def test_names_in_all_list_count_as_claims(tmp_path: Path) -> None:
    """``__all__`` 里登记的名字即使由 ``__getattr__`` 动态给出，也算这一格的合法声明。"""
    modules, dotted = _fake_module(
        tmp_path, "star", '__all__ = ["Exported"]\n\n\ndef __getattr__(name):\n    return None\n'
    )
    records = {dotted: f"`Exported`{CLAIM_TAIL}"}
    assert ar._claim_defects(records, modules, min_claims=0) == []


def test_a_thin_claim_set_blinds_the_ruler(tmp_path: Path) -> None:
    """下限：记号被批量擦掉时判据必须自己喊出来，不许把"没声明"读成"没缺陷"。

    同一份 records 在 ``min_claims=0`` 下零缺陷，说明这一格红的是**规模**，
    不是符号真假——被豁免模块全都对，也照样要红。
    """
    modules, dotted = _fake_module(tmp_path, "few", "A = 1\n")
    records = {dotted: f"`A`{CLAIM_TAIL}"}
    assert ar._claim_defects(records, modules, min_claims=0) == []
    defects = ar._claim_defects(records, modules)
    assert [d.split("]")[0] for d in defects] == ["[blind-claims"], defects
