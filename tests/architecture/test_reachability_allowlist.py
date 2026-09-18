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


#: 够长且合格的理由样本——各缺陷用例只在"模块对不对/是否重复"这一维度上失败。
LONG_REASON = "公共 API：由用户显式 import，内核不 import 属预期，测试逐条覆盖其语义与边界"


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
# 四类记录缺陷各自都被抓住
# --------------------------------------------------------------------------- #
def test_thin_reason_is_a_defect(tmp_path: Path) -> None:
    path = _write(tmp_path, "tstdx.batch  # 太短")
    records, defects = ar._load_allow(path)
    assert list(records) == ["tstdx.batch"]
    assert len(defects) == 1 and defects[0].startswith("[thin]")


def test_duplicate_entry_is_a_defect(tmp_path: Path) -> None:
    reason = "公开 API：由用户显式 import，内核不 import 属预期，测试逐条覆盖其语义"
    path = _write(tmp_path, f"tstdx.batch  # {reason}", f"tstdx.batch  # {reason}")
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
