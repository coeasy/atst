"""Contract Automation 回归保护（v14 Phase 4）。

把 scripts/contract_audit.py 的审计逻辑以 pytest 形态固化，防止
Typed Query / Registry / Domain Record 三者在后续迭代中漂移。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


class TestContractAutomation:
    def test_registry_coverage_no_orphans(self) -> None:
        """Typed Query capability 必须都存在于 PROVIDERS 注册表。"""
        from tstdx.providers import PROVIDERS

        registered: set[str] = set()
        for pid in PROVIDERS.ids():
            registered |= set(PROVIDERS.get(pid).capabilities())

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
        for name in dir(tq):
            cls = getattr(tq, name)
            if not isinstance(cls, type) or name in skip:
                continue
            if not dc.is_dataclass(cls):
                continue
            try:
                instance = cls()
            except Exception:
                continue
            capability = getattr(instance, "capability", None)
            if isinstance(capability, str):
                assert capability in registered, (
                    f"Typed Query {name} capability={capability!r} 不在 PROVIDERS 注册表"
                )

    def test_every_business_capability_has_typed_query(self) -> None:
        """注册表中的业务 capability 必须有 Typed Query 契约。"""
        import dataclasses as dc

        import tstdx.typed_query as tq
        from tstdx.catalog.capability import MIGRATED_CAPABILITIES
        from tstdx.providers import PROVIDERS

        internal = {
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
        # v13 migrated-catalog capabilities are reached through the generic
        # ``query_capability`` gateway / ``Client.call`` contract rather than a
        # dedicated Typed Query (see integration/mcp/_tools_spec.py). They are a
        # declared contract of their own, so they are not "missing" a Typed Query.
        internal |= set(MIGRATED_CAPABILITIES)
        registered: set[str] = set()
        for pid in PROVIDERS.ids():
            registered |= set(PROVIDERS.get(pid).capabilities())

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
        typed: set[str] = set()
        for name in dir(tq):
            cls = getattr(tq, name)
            if not isinstance(cls, type) or name in skip:
                continue
            if not dc.is_dataclass(cls):
                continue
            for kwargs in (
                {},
                {"question": "x"},
                {"condition": "x"},
                {"keyword": "x"},
                {"index_code": "000300"},
                {"board_id": "BK0475"},
                {"codes": ("000001",)},
            ):
                try:
                    instance = cls(**kwargs)
                except Exception:
                    continue
                capability = getattr(instance, "capability", None)
                if isinstance(capability, str):
                    typed.add(capability)
                    break

        missing = sorted((registered - internal) - typed)
        assert not missing, f"注册业务 capability 缺少 Typed Query: {missing}"

    def test_all_typed_queries_semantic_ready(self) -> None:
        """全部 Typed Query 契约必须 semantic_ready。"""
        from tstdx.typed_query import (
            BoardMemberQuery,
            FundBaseInfoMultiQuery,
            IndexConstituentsQuery,
            ScreeningQuery,
            SuggestQuery,
            WencaiQuery,
        )

        minimal_instances = {
            WencaiQuery: lambda: WencaiQuery(query="x"),
            ScreeningQuery: lambda: ScreeningQuery(query="x"),
            SuggestQuery: lambda: SuggestQuery(key="x"),
            IndexConstituentsQuery: lambda: IndexConstituentsQuery(index="000300"),
            BoardMemberQuery: lambda: BoardMemberQuery(node="BK0475"),
            FundBaseInfoMultiQuery: lambda: FundBaseInfoMultiQuery(codes=("000001",)),
        }

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
        checked = 0
        for name in dir(tq):
            cls = getattr(tq, name)
            if not isinstance(cls, type) or name in skip:
                continue
            if not dc.is_dataclass(cls):
                continue
            factory = minimal_instances.get(cls, cls)
            try:
                instance = factory()
            except Exception:
                continue
            if not isinstance(getattr(instance, "capability", None), str):
                continue
            checked += 1
            assert getattr(instance, "semantic_ready", False), f"{name} semantic_ready=False"
        assert checked >= 60, f"Typed Query 契约数异常: {checked}"

    def test_every_typed_capability_has_domain_record(self) -> None:
        """每个 Typed Query capability 有对应 Domain Record 映射。"""
        import dataclasses as dc

        import tstdx.typed_query as tq
        from tstdx.typed_query import record_type_for

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
        caps: set[str] = set()
        for name in dir(tq):
            cls = getattr(tq, name)
            if not isinstance(cls, type) or name in skip:
                continue
            if not dc.is_dataclass(cls):
                continue
            for kwargs in (
                {},
                {"question": "x"},
                {"condition": "x"},
                {"keyword": "x"},
                {"index_code": "000300"},
                {"board_id": "BK0475"},
                {"codes": ("000001",)},
            ):
                try:
                    instance = cls(**kwargs)
                except Exception:
                    continue
                capability = getattr(instance, "capability", None)
                if isinstance(capability, str):
                    caps.add(capability)
                    break

        missing = sorted(cap for cap in caps if record_type_for(cap) is None)
        assert not missing, f"Typed capability 缺少 Domain Record 映射: {missing}"

    def test_cli_script_exits_zero(self) -> None:
        """scripts/contract_audit.py --ci 必须退出 0。"""
        script = _ROOT / "scripts" / "contract_audit.py"
        assert script.exists(), f"脚本缺失: {script}"
        result = subprocess.run(
            [sys.executable, str(script), "--ci"],
            capture_output=True,
            text=True,
            cwd=str(_ROOT),
        )
        assert result.returncode == 0, (
            f"contract_audit --ci 失败 rc={result.returncode}\n"
            f"{result.stdout[-800:]}\n{result.stderr[-400:]}"
        )
