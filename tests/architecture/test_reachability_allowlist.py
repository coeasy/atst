# Copyright (c) 2026 tstdx contributors
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
    assert {"tstdx.cli", "tstdx.__main__", "tstdx.tools.spec_audit"} <= entry
    assert "tstdx.runtime.kernel" not in entry
    records, _ = ar._load_allow()
    assert not ({"tstdx.cli", "tstdx.__main__"} & set(records))


# --------------------------------------------------------------------------- #
# 六类记录缺陷各自都被抓住
# --------------------------------------------------------------------------- #
def test_thin_reason_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, "tstdx.batch  # 太短")
    records, defects = ar._load_allow(path)
    assert list(records) == ["tstdx.batch"]
    assert len(defects) == 1 and defects[0].startswith("[thin]")


def test_duplicate_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"tstdx.batch  # {LONG_REASON}", f"tstdx.batch  # {LONG_REASON}")
    _, defects = ar._load_allow(path)
    assert [d.split("]")[0] for d in defects] == ["[dup"]


def test_dead_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"tstdx.nope  # {LONG_REASON}")
    records, parse_defects = ar._load_allow(path)
    assert parse_defects == []
    defects = ar._allowlist_defects(records, {"tstdx.batch"}, set())
    assert [d.split("]")[0] for d in defects] == ["[dead"]


def test_stale_entry_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, f"tstdx.batch  # {LONG_REASON}")
    records, _ = ar._load_allow(path)
    defects = ar._allowlist_defects(records, {"tstdx.batch"}, {"tstdx.batch"})
    assert [d.split("]")[0] for d in defects] == ["[stale"]


# --------------------------------------------------------------------------- #
# "谁消费它"必须是可核验的指针，不是自由文本
# --------------------------------------------------------------------------- #
def test_reason_pointing_at_a_removed_path_is_a_defect(tmp_path: Path) -> None:
    """豁免理由引用的目录被改名/删除后，这条证据就已经不成立，必须当场判红。"""
    path = _write(
        tmp_path,
        "tstdx.batch  # 由 tests/this_directory_was_renamed_long_ago/ 与它的测试消费，"
        "内核不 import 属预期，理由本身够长不会被 thin 抢先",
    )
    _, defects = ar._load_allow(path)
    assert [d.split("]")[0] for d in defects] == ["[dead-pointer"]


def test_reason_without_any_verifiable_pointer_is_a_defect(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "tstdx.batch  # 这是一段说得很长很圆、却一个文件都举不出来的自由文本理由，"
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
