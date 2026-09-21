#!/usr/bin/env python3
"""v14 Contract Automation —— 对账 Registry 的声明形状与 Typed Query/Record 的一致性。

中期 P2（REFACTOR_PLAN_v14_FULL_UPGRADE Phase 4 Contract Automation）。
校验规则（括号内为不满足时的级别）：

1. **Registry 覆盖**（ERROR）：注册表里每个 capability 必须落在某个**派生**的声明形状内——
   有专属 Typed Query 契约、或在通用迁移网关/内核专属执行体上。三者之外的才算缺口。
   旧口径把"注册表有、契约没有"一律记 PENDING（不阻断），而实测那些能力**全部**走迁移网关：
   这种 PENDING 既掩盖真缺口，又把"要不要补专属契约"说成一个已知尺寸的待办面。
   规模数字只在报告行里现算现印，散文（含本 docstring）不抄任何计数。
   反方向照旧是 ERROR：契约指向注册表里不存在的 capability ＝ 对外承诺一条没有命令的查询。
2. **语义就绪**（ERROR）：每个 Typed Query 契约 ``semantic_ready=True``
   （即同一个 capability 已出现在 canonical Provider 注册表）。
3. **编译通过**（ERROR）：每个 Typed Query 可经 ``call_payload_from_typed`` + ``QueryPlanner``
   编译为唯一 ``QueryPlan``（Typed Query = Kernel boundary）。
4. **Domain Record 映射**（PENDING）：每个 Typed Query capability 有对应的
   Domain Record 类型（Typed Query = Domain Result Model）。
5. **Record 往返无损**（ERROR）：每个 Domain Record ``to_dict`` -> ``from_dict``
   往返保持核心字段（Domain Result Model = Serialization）。

用法::

    python scripts/contract_audit.py            # 全量审计（恒 exit 0，仅打印）
    python scripts/contract_audit.py --ci       # 存在 ERROR 级缺口时 exit 1（PENDING 不阻断）

退出码: 0=无 ERROR 级缺口（可能仍有 PENDING 待办），1=--ci 且存在 ERROR
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# 允许从任意 CWD / scripts 目录直接运行
_ROOTS = (
    Path(__file__).resolve().parents[1],
    Path.cwd(),
)
for _root in _ROOTS:
    if (_root / "tstdx").is_dir() and str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
        break


# --------------------------------------------------------------------------- #
# 能力覆盖的三个来源：注册表 / 专属契约 / 通用派发面（全部现算，不维护手抄名单）
# --------------------------------------------------------------------------- #
def registered_capabilities() -> set[str]:
    """PROVIDERS 注册表声明的全部 capability 名。"""
    from tstdx.providers import PROVIDERS

    out: set[str] = set()
    for pid in PROVIDERS.ids():
        out |= set(PROVIDERS.get(pid).capabilities())
    return out


def gateway_capabilities() -> set[str]:
    """不需要专属 Typed Query 的那些：走通用迁移网关或内核专属执行体。

    两个来源都是生产表自身的投影——``MIGRATED_CAPABILITIES`` 由注册表派生的迁移绑定算出，
    ``DIRECT_BINDINGS`` 里 ``executor_name != "_migrated_capability"`` 的是内核自带的协议
    原语（quotes/bars/snapshot/security_count/security_list）。这里原本是一份 17 个名字的
    手抄名单，其中 12 个与迁移表重复：手抄的那部分等于"名单说了算"，某个名字被摘掉绑定或
    改了名，审计不会有任何反应。
    """
    from tstdx.catalog.capability import MIGRATED_CAPABILITIES
    from tstdx.runtime.executor import DIRECT_BINDINGS

    dedicated = {
        binding.capability
        for binding in DIRECT_BINDINGS
        if binding.executor_name != "_migrated_capability"
    }
    return set(MIGRATED_CAPABILITIES) | dedicated


def typed_capabilities() -> set[str]:
    """有专属 Typed Query 契约的 capability（口径见 :func:`_all_typed_queries`）。"""
    return _typed_capabilities()


#: 三个来源各自的规模下限。任一来源解析失败都会把"没有缺口"读成假绿，所以判据
#: 必须自己盯住尺子的刻度（同 ``scripts/audit_reachability.py`` 的 ``[blind-claims]``）。
MIN_REGISTERED = 150
MIN_TYPED_COVERAGE = 50
MIN_GATEWAY_COVERAGE = 150


def _all_typed_queries() -> list[type]:
    """收集 typed_query 模块中全部具体 Query 契约类。

    具体与否按结构判据判定（可构造 + `capability` 为字符串），不靠手抄名单：抽象基类
    （根 `CapabilityQuery`、9 个领域基类、`BatchCapabilityQuery`、`TypedQueryResult`）
    因缺必填参数无法构造，本就被排除；写死名单会在新增基类时把它静默算成契约。
    """
    import dataclasses as dc

    import tstdx.typed_query as tq

    out: list[type] = []
    for name in dir(tq):
        cls = getattr(tq, name)
        if not isinstance(cls, type):
            continue
        if not dc.is_dataclass(cls):
            continue
        try:
            inst = _minimal_instance(cls)
        except Exception:
            continue
        capability = getattr(inst, "capability", None)
        if isinstance(capability, str):
            out.append(cls)
    return out


def _typed_capabilities() -> set[str]:
    """所有 Typed Query 契约为之定义 capability 的集合。"""
    result: set[str] = set()
    for cls in _all_typed_queries():
        inst = _minimal_instance(cls)
        result.add(inst.capability)
    return result


def _minimal_instance(cls: type) -> Any:
    """为带必填字段的领域 Query 构造最小合法实例。"""
    from tstdx.typed_query import (
        BoardMemberQuery,
        FundBaseInfoMultiQuery,
        IndexConstituentsQuery,
        ScreeningQuery,
        SuggestQuery,
        WencaiQuery,
    )

    if cls is WencaiQuery:
        return WencaiQuery(query="x")
    if cls is ScreeningQuery:
        return ScreeningQuery(query="x")
    if cls is SuggestQuery:
        return SuggestQuery(key="x")
    if cls is IndexConstituentsQuery:
        return IndexConstituentsQuery(index="000300")
    if cls is BoardMemberQuery:
        return BoardMemberQuery(node="BK0475")
    if cls is FundBaseInfoMultiQuery:
        return FundBaseInfoMultiQuery(codes=("000001",))
    return cls()


# --------------------------------------------------------------------------- #
# 审计点
# --------------------------------------------------------------------------- #
def audit_registry_coverage() -> list[str]:
    """每个注册 capability 要么有专属 Typed Query，要么在已声明的派发面上。

    早先这一格对"注册表有、契约没有"只报 PENDING（92 条，不阻断），于是新增一个
    没形状的能力与把它写进台账是同一个读数。现在按**来源**判：能被三个派生集合
    （注册表 / Typed 契约 / 通用派发面）覆盖之外的那个能力，才是真缺口，按 ERROR 阻断。
    规模下限是这条判据的自检——三个来源任一解析失败都会让"零缺口"变成假绿。
    """
    problems: list[str] = []
    registered = registered_capabilities()
    typed = typed_capabilities()
    gateway = gateway_capabilities()

    uncovered = sorted(registered - typed - gateway)
    orphan_typed = sorted(typed - registered)

    for cap in uncovered:
        problems.append(
            f"ERROR: registry capability {cap!r} 既无 Typed Query 契约，也不在任何派发面上"
        )
    for cap in orphan_typed:
        problems.append(f"ERROR: Typed Query capability {cap!r} 不在 PROVIDERS 注册表")
    for label, got, floor in (
        ("注册 capability", len(registered), MIN_REGISTERED),
        ("Typed Query 契约", len(typed), MIN_TYPED_COVERAGE),
        ("通用派发面", len(gateway), MIN_GATEWAY_COVERAGE),
    ):
        if got < floor:
            problems.append(
                f"ERROR: {label}只算出 {got} 个（下限 {floor}）：来源没解析出来，覆盖结论不可信"
            )
    return problems


def audit_semantic_ready() -> list[str]:
    """全部 Typed Query 契约语义就绪。"""
    problems: list[str] = []
    for cls in _all_typed_queries():
        try:
            inst = _minimal_instance(cls)
        except Exception as exc:
            problems.append(f"ERROR: {cls.__name__} 构造失败（{type(exc).__name__}: {exc}）")
            continue
        try:
            ok = inst.semantic_ready
        except Exception as exc:
            ok = False
            problems.append(
                f"ERROR: {cls.__name__} semantic_ready 探测异常（{type(exc).__name__}: {exc}）"
            )
            continue
        if not ok:
            problems.append(f"ERROR: {cls.__name__}（{inst.capability}）semantic_ready=False")
    return problems


def audit_typed_kernel_compilation() -> list[str]:
    """全部 Typed Query 可经 call_payload_from_typed + QueryPlanner 编译为 QueryPlan。"""
    from tstdx.catalog.capability import default_provider_for
    from tstdx.query import QueryPlanner, QuerySpec
    from tstdx.typed_query import call_payload_from_typed

    planner = QueryPlanner()
    problems: list[str] = []
    for cls in _all_typed_queries():
        try:
            inst = _minimal_instance(cls)
        except Exception:
            continue
        try:
            payload = call_payload_from_typed(inst)
            spec = QuerySpec.build(
                inst.capability,
                provider=inst.provider or default_provider_for(inst.capability),
                options={"args": [], "kwargs": payload},
            )
            plan = planner.compile(spec)
            if plan.spec.capability != inst.capability:
                problems.append(
                    f"ERROR: {cls.__name__} capability 不匹配"
                    f"（expected={inst.capability!r}, got={plan.spec.capability!r}）"
                )
        except Exception as exc:
            problems.append(f"ERROR: {cls.__name__} 内核编译失败（{type(exc).__name__}: {exc}）")
    return problems


def audit_domain_records() -> list[str]:
    """每个 Typed Query capability 有 Domain Record 映射。"""
    from tstdx.typed_query import record_type_for

    problems: list[str] = []
    for cap in _typed_capabilities():
        if record_type_for(cap) is None:
            problems.append(f"PENDING: capability {cap!r} 无 Domain Record 映射")
    return problems


def audit_record_roundtrip() -> list[str]:
    """每个 Domain Record to_dict -> from_dict 往返无损。"""
    from tstdx.domain.records import (
        BondRecord,
        FinancialRecord,
        FundRecord,
        MacroRecord,
        MarketDataRecord,
        NewsRecord,
        OptionRecord,
        ResearchRecord,
        SearchRecord,
    )

    problems: list[str] = []

    def check(name: str, record: Any, core: Sequence[str]) -> None:
        try:
            data = record.to_dict()
            restored = type(record).from_dict(data)
            for key in core:
                left = getattr(record, key)
                right = getattr(restored, key)
                if left != right:
                    problems.append(f"ERROR: {name}.{key} 往返不一致（{left!r} != {right!r}）")
        except Exception as exc:
            problems.append(f"ERROR: {name} 往返失败（{type(exc).__name__}: {exc}）")

    check(
        "FinancialRecord",
        FinancialRecord(code="600519.SH", report_date="2026-06-30"),
        ("code", "report_date"),
    )
    check(
        "FundRecord",
        FundRecord(code="110011", name="基金A", fund_type=1),
        ("code", "name", "fund_type"),
    )
    check(
        "BondRecord",
        BondRecord(code="113001.SH", name="转债", price=118.5),
        ("code", "name", "price"),
    )
    check("NewsRecord", NewsRecord(id="n1", title="标题", source="sina"), ("id", "title", "source"))
    check(
        "ResearchRecord",
        ResearchRecord(code="600519.SH", title="研报", org="中信", target_price=2000.0),
        ("code", "title", "org"),
    )
    check("OptionRecord", OptionRecord(code="10000001", strike=1800.0), ("code", "strike"))
    check(
        "MarketDataRecord",
        MarketDataRecord(kind="hot_rank", code="1", name="茅台"),
        ("kind", "code", "name"),
    )
    check("SearchRecord", SearchRecord(code="600519", name="贵州茅台"), ("code", "name"))
    check(
        "MacroRecord",
        MacroRecord(kind="fx_rates", key="USDCNY", value=7.13),
        ("kind", "key", "value"),
    )
    return problems


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
_AUDITS: tuple[tuple[str, Callable[[], list[str]]], ...] = (
    ("Registry 覆盖", audit_registry_coverage),
    ("Semantic 就绪", audit_semantic_ready),
    ("Typed 编译", audit_typed_kernel_compilation),
    ("Domain Record", audit_domain_records),
    ("Record 往返", audit_record_roundtrip),
)


def run(ci: bool = False) -> int:
    lines: list[str] = []
    registered = registered_capabilities()
    typed = _typed_capabilities()
    gateway = gateway_capabilities()
    lines.append("=" * 64)
    lines.append("v14 Contract Automation 审计")
    lines.append(f"{len(_all_typed_queries())} 个 Typed Query 契约")
    lines.append(f"{len(registered)} 个注册 capability")
    lines.append(
        f"形状来源：{len(typed)} 个有专属契约 / {len(gateway)} 个在通用派发面 / "
        f"{len(registered - typed - gateway)} 个无任何声明形状（这一格才是缺口）"
    )
    lines.append("=" * 64)

    errors: list[str] = []
    warnings: list[str] = []
    for name, audit in _AUDITS:
        lines.append(f"\n[{name}]")
        problems = audit()
        if not problems:
            lines.append("  OK")
            continue
        for problem in problems:
            lines.append(f"  {problem}")
            if problem.startswith("ERROR"):
                errors.append(problem)
            else:
                warnings.append(problem)

    lines.append("\n" + "=" * 64)
    if errors:
        lines.append(f"FAIL: {len(errors)} 个错误")
        for e in errors:
            lines.append(f"  {e}")
    elif warnings:
        lines.append(f"WARN: {len(warnings)} 个待办（不影响 CI，但建议补齐）")
        for w in warnings:
            lines.append(f"  {w}")
    else:
        lines.append(
            f"PASS: {len(registered)} 个注册 capability 全部落在声明形状之内"
            f"（专属 Typed Query {len(typed)} ∪ 通用派发面 {len(gateway)}）"
        )
    lines.append("=" * 64)

    print("\n".join(lines))
    if ci and errors:
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v14 Contract Automation 审计")
    parser.add_argument("--ci", action="store_true", help="CI 模式：存在错误即退出 1")
    args = parser.parse_args()
    sys.exit(run(ci=args.ci))
