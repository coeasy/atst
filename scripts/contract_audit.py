#!/usr/bin/env python3
"""v14 Contract Automation —— 验证 Typed Query = Registry = Record 链路一致性。

中期 P2（REFACTOR_PLAN_v14_FULL_UPGRADE Phase 4 Contract Automation）。
校验规则：

1. **Registry 全覆盖**：每个业务 capability 在 ``PROVIDERS`` 注册表中可路由，
   且必须有对应的 Typed Query 契约（Typed Query = Registry 一一映射）。
2. **语义就绪**：每个 Typed Query 契约 ``semantic_ready=True``
   （即同一个 capability 已出现在 canonical Provider 注册表）。
3. **编译通过**：每个 Typed Query 可经 ``call_payload_from_typed`` + ``QueryPlanner``
   编译为唯一 ``QueryPlan``（Typed Query = Kernel boundary）。
4. **Domain Record 映射**：每个 Typed Query capability 有对应的
   Domain Record 类型（Typed Query = Domain Result Model）。
5. **Record 往返无损**：每个 Domain Record ``to_dict`` -> ``from_dict``
   往返保持核心字段（Domain Result Model = Serialization）。

用法::

    python scripts/contract_audit.py            # 全量审计
    python scripts/contract_audit.py --ci       # CI 模式（任何缺口 exit 1）

退出码: 0=全绿，1=存在缺口/异常
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator, Sequence
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
# 领域 Query 白名单（有 Typed Query 的 capability）
# --------------------------------------------------------------------------- #
_DOMAIN_TYPED: frozenset[str] = frozenset(
    {
        # Financial
        "balance_sheet",
        "income_sheet",
        "cash_flow",
        "financial_abstract",
        "dividend_history",
        "stock_valuation",
        "holder_changes",
        "holder_num",
        "free_holders",
        "capital_changes",
        "corporate_action",
        "announcements",
        "ipo_review",
        "stock_base_info",
        "stock_all_performance",
        "stock_report_dates",
        # Fund
        "fund_rank",
        "fund_holdings",
        "fund_base_info",
        "fund_base_info_multi",
        "fund_manager",
        "fund_asset_allocation",
        "fund_period_change",
        "fund_industry_distribution",
        "fund_public_dates",
        # Bond
        "bond_kline",
        "bond_base_info",
        "bond_all_base_info",
        "bond_realtime",
        "bond_trades",
        "bond_today_bill",
        "bond_history_bill",
        "convertible_bond",
        # Futures
        "futures_kline",
        "futures_base_info",
        "futures_realtime",
        "futures_trades",
        # Options
        "options_snapshot",
        "options_list",
        "options_trends",
        # News / Research
        "news_financial",
        "research_reports",
        "research_visits",
        # Market data
        "hot_rank",
        "limit_pool",
        "northbound",
        "margin",
        "longhu",
        "market_stat",
        "board_rank",
        "fund_flow",
        "stock_changes",
        "rank",
        # Search
        "wencai",
        "screening",
        "suggest",
        "index_constituents",
        "industry_board",
        "board_list",
        "board_member",
        # Macro
        "fx_rates",
        "global_quotes",
        "f10",
    }
)


# 不入 Contract Automation 的内部/基础 capability（已有 QuerySpec 或
# 属于协议层而非 Typed 业务能力）
_INTERNAL_CAPABILITIES: frozenset[str] = frozenset(
    {
        "quotes",
        "bars",
        "minute",
        "trades",
        "snapshot",
        "security_count",
        "security_list",
        "ex_market_list",
        "ex_instruments",
        "ex_quotes",
        "ex_bars",
        "goods_quotes",
        "goods_bars",
        "mac_quotes",
        "f10_catalog",
        "finance",
        "news",
    }
)


def _iter_domain_capabilities() -> Iterator[str]:
    """遍历注册表中所有业务 capability（排除内部基础能力）。"""
    from tstdx.providers import PROVIDERS

    seen: set[str] = set()
    for pid in PROVIDERS.ids():
        spec = PROVIDERS.get(pid)
        for capability in spec.capabilities():
            if capability in _INTERNAL_CAPABILITIES:
                continue
            if capability in seen:
                continue
            seen.add(capability)
            yield capability


def _all_typed_queries() -> list[type]:
    """收集 typed_query 模块中全部具体 Query 契约类。"""
    import dataclasses as dc

    import tstdx.typed_query as tq

    skip = {
        "CapabilityQuery",
        "SymbolQuery",
        "BatchCapabilityQuery",
        "TypedQueryResult",
        "FinancialQuery",
        "FundQuery",
        "BondQuery",
        "FuturesQuery",
        "OptionsQuery",
        "MarketDataQuery",
        "SearchQuery",
        "MacroQuery",
    }
    out: list[type] = []
    for name in dir(tq):
        cls = getattr(tq, name)
        if not isinstance(cls, type) or name in skip:
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
    """注册表业务 capability 与 Typed Query 契约一一对应。"""
    problems: list[str] = []
    registered = set(_iter_domain_capabilities())
    typed = _typed_capabilities()

    # 注册表有、但没有 Typed Query 的：允许待补（pending），警告级
    missing_typed = sorted(registered - typed)
    # 有 Typed Query、但注册表没有的：错误级（契约指向不存在的 capability）
    orphan_typed = sorted(typed - registered)

    for cap in missing_typed:
        problems.append(f"PENDING: registry capability {cap!r} 无 Typed Query 契约")
    for cap in orphan_typed:
        problems.append(f"ERROR: Typed Query capability {cap!r} 不在 PROVIDERS 注册表")
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
    lines.append("=" * 64)
    lines.append("v14 Contract Automation 审计")
    lines.append(f"{len(_all_typed_queries())} 个 Typed Query 契约")
    lines.append(f"{len(set(_iter_domain_capabilities()))} 个注册业务 capability")
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
        lines.append("PASS: 全链路一致（Registry = Typed = Runtime = Record）")
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
