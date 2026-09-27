"""Contract Automation 回归保护（v14 Phase 4）。

把 scripts/contract_audit.py 的审计逻辑以 pytest 形态固化，防止
Typed Query / Registry / Domain Record 三者在后续迭代中漂移。

**判据只有一份实现**：这里不再自己遍历 `atst.typed_query` 猜"哪些是具体契约"，而是加载
脚本、复用它的派生口径。同一件事在本文件里曾抄过 4 份，其中 2 份各带一份手抄的"必填参数怎么填"
名单（`{"index_code": "000300"}` 这类）——契约类一改构造函数，副本会各自静默地少算几个。
"具体契约"仍按结构判据识别（可构造 + `capability` 为字符串），所以没有任何基类名单：抽象基类
因缺必填参数构造不出来，本就被排除（算上脚本自己那处，这类副本曾同时存在 5 份）。
最后一个测试保留 subprocess 形态：它钉的是 CLI 参数与退出码这层外部形状，与"同一套口径
算得对不对"不是同一件事。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ca():
    """加载 scripts/contract_audit.py —— 与门禁跑的是同一份实现。"""
    spec = importlib.util.spec_from_file_location(
        "contract_audit_under_test", _ROOT / "scripts" / "contract_audit.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["contract_audit_under_test"] = module
    spec.loader.exec_module(module)
    return module


class TestContractAutomation:
    def test_registry_coverage_no_orphans(self, ca) -> None:
        """Typed Query capability 必须都存在于 PROVIDERS 注册表。"""
        orphan = ca.typed_capabilities() - ca.registered_capabilities()
        assert not orphan, f"契约指向不存在的 capability: {sorted(orphan)}"

    def test_every_registered_capability_has_a_declared_shape(self, ca) -> None:
        """每个注册能力都落在**派生**的声明形状内：专属契约 ∪ 通用派发面。

        取代原先那份 17 个名字的手抄名单：实测其中 12 个与迁移绑定表重复（重复的那部分等于
        "名单说了算"——把某个名字的绑定摘掉，审计不会有反应），另 5 个是内核协议原语，而
        "是不是原语"能从 ``DIRECT_BINDINGS`` 的 executor_name 现算，不必抄。
        """
        uncovered = (
            ca.registered_capabilities() - ca.typed_capabilities() - ca.gateway_capabilities()
        )
        assert not uncovered, f"注册 capability 既无契约也不在任何派发面上: {sorted(uncovered)}"

    def test_the_coverage_sources_have_floors(self, ca) -> None:
        """当前三个来源确实长在下限之上——下限不是为凑数写的。"""
        assert len(ca.registered_capabilities()) >= ca.MIN_REGISTERED
        assert len(ca.typed_capabilities()) >= ca.MIN_TYPED_COVERAGE
        assert len(ca.gateway_capabilities()) >= ca.MIN_GATEWAY_COVERAGE

    def test_a_collapsed_registry_source_is_reported(self, ca, monkeypatch) -> None:
        """注册表整个解析不出来时，"零缺口"是假绿：判据必须因规模下限而红。

        这条不是检查当前读数，而是检查**下限本身有没有牙**——把 ``MIN_REGISTERED``
        调成 0，本测试就会红（变异证据见重构方案第 7 轮）。
        """
        monkeypatch.setattr(ca, "registered_capabilities", lambda: {"quotes"})
        problems = ca.audit_registry_coverage()
        assert any("注册 capability" in p and p.startswith("ERROR") for p in problems), problems

    def test_a_collapsed_typed_source_is_reported(self, ca, monkeypatch) -> None:
        """契约来源解析不出来时缺口会算成 0（派发面盖住一切），只有下限认得出来。"""
        monkeypatch.setattr(ca, "typed_capabilities", lambda: {"quotes"})
        problems = ca.audit_registry_coverage()
        assert any("Typed Query 契约" in p and p.startswith("ERROR") for p in problems), problems

    def test_a_capability_with_no_shape_at_all_fails_the_audit(self, ca, monkeypatch) -> None:
        """正控：注册表多出一个"没契约、也没接线"的能力，审计必须报 ERROR。"""
        real = ca.registered_capabilities()
        monkeypatch.setattr(ca, "registered_capabilities", lambda: real | {"phantom_shapeless"})
        problems = ca.audit_registry_coverage()
        hits = [p for p in problems if p.startswith("ERROR") and "phantom_shapeless" in p]
        assert hits, f"新登记的无形状能力没被抓住：{problems}"

    def test_masking_the_dispatch_face_exposes_the_gap(self, ca, monkeypatch) -> None:
        """反面对照：把派发面遮成空，缺口必须现形，不许被读成"没缺陷"。"""
        monkeypatch.setattr(ca, "gateway_capabilities", lambda: set())
        problems = ca.audit_registry_coverage()
        reported = {p.split("'")[1] for p in problems if "既无 Typed Query 契约" in p}
        assert reported == ca.registered_capabilities() - ca.typed_capabilities(), problems

    def test_all_typed_queries_semantic_ready(self, ca) -> None:
        """全部 Typed Query 契约语义就绪。"""
        assert ca.audit_semantic_ready() == []

    def test_every_typed_capability_has_domain_record(self, ca) -> None:
        """每个 Typed Query capability 有对应 Domain Record 映射。"""
        assert ca.audit_domain_records() == []

    def test_coverage_sources_agree_with_production_tables(self, ca) -> None:
        """派发面确实来自生产表，而不是脚本自己另算了一份。"""
        from atst.catalog.capability import MIGRATED_CAPABILITIES
        from atst.runtime.executor import DIRECT_BINDINGS

        gateway = ca.gateway_capabilities()
        dedicated = {
            b.capability for b in DIRECT_BINDINGS if b.executor_name != "_migrated_capability"
        }
        assert set(MIGRATED_CAPABILITIES) <= gateway
        assert dedicated <= gateway
        # 内核协议原语（既不在迁移表也无专属契约）必须由专属执行体解释，不能靠名单
        primitives = ca.registered_capabilities() - set(MIGRATED_CAPABILITIES)
        assert primitives <= dedicated, (
            f"无网关绑定的能力不在专属执行体上: {primitives - dedicated}"
        )

    def test_cli_script_exits_zero(self) -> None:
        """scripts/contract_audit.py --ci 必须退出 0。"""
        script = _ROOT / "scripts" / "contract_audit.py"
        assert script.exists(), f"脚本缺失: {script}"
        result = subprocess.run(
            [sys.executable, str(script), "--ci"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            cwd=str(_ROOT),
        )
        assert result.returncode == 0, (
            f"contract_audit --ci 失败 rc={result.returncode}\n"
            f"{result.stdout[-800:]}\n{result.stderr[-400:]}"
        )
