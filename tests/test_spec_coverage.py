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

from dataclasses import replace
from pathlib import Path

import pytest

from tstdx.tools.codegen import load_all_specs
from tstdx.tools.spec_audit import (
    audit_all,
    coverage_summary,
    draft_spec_files,
    is_payloadless,
    spec_files,
)

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


# --------------------------------------------------------------------------- #
# v17 Phase 5：审计口径修正（分母完整性 + 分族证据），防回潮
# --------------------------------------------------------------------------- #
ALL_RESULTS = audit_all("PROTOCOL_SPEC")


def test_audit_covers_every_non_probe_yaml() -> None:
    """分母 = 全部非自动探测 YAML 文件数，跨族同 spec_id 不得互相吞掉。

    旧实现走 ``load_all_specs``（以 spec_id 为键）：``TRADE/0x0001`` 吃掉
    ``F10/0x0001``、``TRADE/0x0100`` 吃掉 ``7727/0x0100``，两条命令从此不进
    审计，strict 门禁却在它们身上永远绿灯。
    """
    files = spec_files("PROTOCOL_SPEC")
    assert len(ALL_RESULTS) == len(files) == 44, (len(ALL_RESULTS), len(files))
    audited = {r.spec_file for r in ALL_RESULTS}
    assert "PROTOCOL_SPEC/F10/0x0001_F10_CATALOG.yaml" in audited
    assert "PROTOCOL_SPEC/7727/0x0100_EX_MARKET_COUNT.yaml" in audited
    # 排除项可枚举（不是静默丢弃）。这里刻意**不写死路径清单**：跑一次自动探测就会
    # 落下若干未入库的 ``_sniffer/**/DRAFT.yaml``（.gitignore 第 85 行），钉死清单等于
    # 把某一次跑包的现场写进门禁——换一棵树就红（第 18 轮实测：1 份变 4 份）。
    # 真正的不变量是"被排除的必须长成探测产物的样子"：一张手写 spec 悄悄掉出
    # 分母，这条仍然会响。
    drafts = draft_spec_files("PROTOCOL_SPEC")
    assert "PROTOCOL_SPEC/_sniffer/f10/06b9/DRAFT.yaml" in drafts, "排除名单瞎了"
    offenders = [
        relative
        for relative in drafts
        if ("/_sniffer/" not in relative and "/UNKNOWN/" not in relative)
        or not relative.endswith("/DRAFT.yaml")
    ]
    assert offenders == [], f"非探测产物的 YAML 被排除出审计分母：{offenders}"


def test_control_frame_exemption_is_derived_from_spec() -> None:
    """免解析器判定来自 spec 自己声明的空响应，不是硬编码命令号清单。"""
    assert is_payloadless({"response": {"header": [], "fields": [], "record_size": 0}})
    assert not is_payloadless({"response": {"fields": [{"name": "price"}]}})
    assert not is_payloadless({"response": {"fields": [], "record_size": 28}})
    assert not is_payloadless({})
    exempt = sorted(r.spec_id for r in ALL_RESULTS if r.control_frame)
    assert exempt == ["0x0004", "0x000D"]
    assert all(r.covered for r in ALL_RESULTS if r.control_frame)


def test_trade_plane_specs_are_audited_against_trade_anchors() -> None:
    """交易族查自己的账本与帧层，而不是被当成 7709 命令误报"未登记"。"""
    from tstdx.tools.spec_audit import _TRADE_IMPLEMENTATION, _attr_exists

    trade = [r for r in ALL_RESULTS if r.plane == "trade"]
    assert {r.spec_id for r in trade} == {
        "0x0001",
        "0x0002",
        "0x0003",
        "0x0100",
        "0x1000",
        "0x1001",
    }
    assert all(r.in_ledger and r.has_parser and r.covered for r in trade)
    for cmd in range(0x1002, 0x1010):  # 锚点表不得凭空多出无 spec 的命令号
        assert (cmd in _TRADE_IMPLEMENTATION) is False
    for const, codec in _TRADE_IMPLEMENTATION.values():
        assert _attr_exists("tstdx.trade.constants", const)
        assert _attr_exists("tstdx.trade.frames", codec)


def test_strict_still_demands_hundred_percent() -> None:
    """阈值一次都没放宽：少一条覆盖，strict 必须红。"""
    from tstdx.tools.spec_audit import main

    assert coverage_summary(ALL_RESULTS)["coverage_pct"] == 100.0
    assert main(["--strict"]) == 0
    broken = [*ALL_RESULTS[:-1], replace(ALL_RESULTS[-1], has_parser=False)]
    summary = coverage_summary(broken)
    assert summary["coverage_pct"] < 100.0
    assert summary["uncovered"] == [f"{ALL_RESULTS[-1].spec_id} {ALL_RESULTS[-1].name}"]
