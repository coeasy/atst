"""Spec 覆盖率契约测试（D1 / A5 · 验收项 #02）。

门禁：每个 PROTOCOL_SPEC/7709/*.yaml 必须 ——
1. 能被零依赖 YAML 解析器加载（spec 语法合法）；
2. spec_id 唯一；
3. 必备字段齐全（spec_id/name/family/request/response）；
4. 命令号登记在 commands.py 账本；
5. stable/verified spec 的 golden 样本真实存在。

注意：``PROTOCOL_SPEC/UNKNOWN/`` 下的 draft spec 为自动探测生成，
不在本测试覆盖范围内（待人工评审升级为正式 spec 后纳入）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tstdx.tools.codegen import load_all_specs
from tstdx.tools.spec_audit import audit_all, coverage_summary

pytestmark = pytest.mark.unit

#: 核心 spec 目录（7709 标准协议族）
SPEC_SUBDIR = "PROTOCOL_SPEC/7709"

SPECS = load_all_specs(SPEC_SUBDIR)


def test_all_specs_load() -> None:
    """全部 8 份核心 YAML 可解析且非空。"""
    assert len(SPECS) == 8, f"应恰好 8 份核心 spec，实际 {len(SPECS)}"
    for spec_id, spec in SPECS.items():
        assert isinstance(spec, dict), spec_id
        assert spec.get("name"), f"{spec_id}: 缺 name"


def test_all_spec_ids_unique() -> None:
    """load_all_specs 以 spec_id 为键 —— 只要数量与文件数一致即无覆盖冲突。"""
    yaml_files = list(Path(SPEC_SUBDIR).glob("*.yaml"))
    assert len(SPECS) == len(yaml_files), (
        f"spec_id 冲突：{len(yaml_files)} 个文件只解析出 {len(SPECS)} 个 id"
    )


@pytest.mark.parametrize("field", ["spec_id", "name", "family", "request", "response", "version"])
def test_required_fields_present(field: str) -> None:
    for spec_id, spec in SPECS.items():
        assert field in spec, f"{spec_id}: 缺少必备字段 {field!r}"


def test_specs_in_ledger() -> None:
    """每个 spec 的命令号都必须在 85 命令账本中。"""
    from tstdx.protocol.commands import get_command

    for spec_id, spec in SPECS.items():
        family_map = {
            "7709": "quotation",
            "7727": "ex_quotation",
            "MAC": "mac_quotation",
            "F10": "f10",
            "GOODS": "goods",
        }
        fam = family_map.get(str(spec.get("family", "7709")), "quotation")
        cmd = get_command(int(str(spec_id), 16), fam)
        assert cmd is not None, f"{spec_id}: 未登记在命令账本"


def test_golden_samples_exist() -> None:
    """spec 引用的 golden 样本文件必须存在（路径以仓库根为基准）。"""
    root = Path(__file__).resolve().parent.parent
    for spec_id, spec in SPECS.items():
        for sample in spec.get("golden_samples") or []:
            p = root / str(sample)
            assert p.is_file(), f"{spec_id}: golden 样本缺失 {sample}"


def test_coverage_summary() -> None:
    """汇总报告：ledger 全过、parser ≥ 5/8（heartbeat/handshake 走握手模块，0x0FC6 为备用号）。"""
    results = audit_all(SPEC_SUBDIR)
    summary = coverage_summary(results)  # 1.2.0: 签名改为复用已有 results（CLI 三处统计免重复解析）
    assert summary["total_specs"] == len(results) == len(SPECS)
    assert summary["in_ledger"] == summary["total_specs"], "全部 spec 必须在账本"
    assert summary["has_parser"] >= 5, f"L1 解析器覆盖不足: {summary['has_parser']}"
    assert summary["coverage_pct"] >= 60.0, f"覆盖率过低: {summary['coverage_pct']}%"
