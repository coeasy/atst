# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3：逐文件覆盖率地板闸（防回归到现状以下）。

``fail_under=77`` 是全包均值门禁，允许整模块裸奔。本判据从最近一次
``coverage xml`` 产物（``coverage.xml``，HEAD 实测）提取每文件实测率并钉地板
（实测向下取整 - 0.01）：任何文件的行覆盖率跌破地板即红。抬高地板必须以
CI 实测为据重新生成这份表（照 ``scripts/gen_coverage_floors.py`` 的口径），
不得手改；重构不得以 bypass 本判据换绿。

coverage.xml 不在场上（如 CI test 作业内）时整条跳过——地板闸在有产物时生效。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

COVERAGE_XML = Path("coverage.xml")

#: 每文件行覆盖率地板（HEAD 实测生成，机器可重算）。键 = coverage.xml filename。
FLOORS: dict[str, float] = {
    "__init__.py": 0.78,
    "__main__.py": 0.00,
    "_version.py": 0.99,
    "batch.py": 0.78,
    "charset/__init__.py": 0.99,
    "charset/encoding.py": 0.85,
    "cli/__init__.py": 0.99,
    "cli/_common.py": 0.96,
    "cli/cmds_hosts.py": 0.93,
    "cli/cmds_kernel.py": 0.96,
    "cli/cmds_market.py": 0.88,
    "cli/cmds_web.py": 0.91,
    "cli/parser.py": 0.99,
    "cli/runtime_commands.py": 0.99,
    "client/__init__.py": 0.99,
    "client/_mixin.py": 0.99,
    "client/_templates_family.py": 0.80,
    "client/_templates_standard.py": 0.88,
    "client/_trampoline.py": 0.97,
    "client/api.py": 0.84,
    "client/async_.py": 0.79,
    "client/core.py": 0.95,
    "client/factory.py": 0.99,
    "client/quick.py": 0.91,
    "client/sync.py": 0.96,
    "codec/__init__.py": 0.99,
    "codec/framing.py": 0.84,
    "codec/primitive.py": 0.95,
    "config/__init__.py": 0.99,
    "config/loader.py": 0.80,
    "config/schema.py": 0.86,
    "diagnostics.py": 0.99,
    "domain/__init__.py": 0.99,
    "domain/adjust.py": 0.85,
    "domain/calendar.py": 0.93,
    "domain/enrich.py": 0.95,
    "domain/finance.py": 0.93,
    "domain/integrity.py": 0.99,
    "domain/models.py": 0.92,
    "domain/period.py": 0.99,
    "domain/records.py": 0.99,
    "domain/symbol.py": 0.96,
    "error_envelope.py": 0.96,
    "errors.py": 0.99,
    "feedback/__init__.py": 0.99,
    "feedback/reporter.py": 0.93,
    "feedback/stats.py": 0.74,
    "feedback/telemetry.py": 0.83,
    "observability/__init__.py": 0.99,
    "observability/metrics.py": 0.88,
    "observability/otel_exporter.py": 0.94,
    "observability/prometheus_exporter.py": 0.92,
    "observability/statsd_exporter.py": 0.85,
    "output/__init__.py": 0.86,
    "profile/__init__.py": 0.99,
    "profile/detect.py": 0.83,
    "profile/presets.py": 0.79,
    "profile/scoring.py": 0.91,
    "protocol/__init__.py": 0.99,
    "protocol/commands.py": 0.97,
    "protocol/generic.py": 0.92,
    "protocol/handshake.py": 0.96,
    "protocol/parsers/__init__.py": 0.99,
    "protocol/parsers/_std7709_bars.py": 0.91,
    "protocol/parsers/_std7709_common.py": 0.95,
    "protocol/parsers/_std7709_quote.py": 0.94,
    "protocol/parsers/f10.py": 0.94,
    "protocol/parsers/goods.py": 0.96,
    "protocol/parsers/mac.py": 0.99,
    "protocol/parsers/std7709.py": 0.99,
    "protocol/parsers/std7709_extra.py": 0.98,
    "protocol/parsers/std7727.py": 0.95,
    "protocol/prober.py": 0.95,
    "protocol/registry.py": 0.95,
    "providers/__init__.py": 0.91,
    "providers/baidu.py": 0.99,
    "providers/boc.py": 0.99,
    "providers/builtin.py": 0.99,
    "providers/cninfo.py": 0.99,
    "providers/contract.py": 0.91,
    "providers/derived.py": 0.99,
    "providers/discovery.py": 0.97,
    "providers/eastmoney.py": 0.99,
    "providers/iwencai.py": 0.99,
    "providers/jsl.py": 0.99,
    "providers/local_vipdoc.py": 0.99,
    "providers/registry.py": 0.93,
    "providers/sina.py": 0.99,
    "providers/tdx.py": 0.99,
    "providers/tencent.py": 0.99,
    "providers/ths.py": 0.99,
    "providers/wallstreet.py": 0.99,
    "query/__init__.py": 0.99,
    "query/fingerprint.py": 0.99,
    "query/planning.py": 0.95,
    "query/spec.py": 0.86,
    "reader/__init__.py": 0.99,
    "reader/formats.py": 0.88,
    "reader/profile.py": 0.88,
    "reader/tdx_config.py": 0.96,
    "result.py": 0.88,
    "runtime/__init__.py": 0.99,
    "runtime/audit.py": 0.94,
    "runtime/autoselect.py": 0.99,
    "runtime/bindings.py": 0.92,
    "runtime/capability_audit.py": 0.99,
    "runtime/capability_contract.py": 0.93,
    "runtime/composed.py": 0.83,
    "runtime/execute_capability.py": 0.93,
    "runtime/execute_channel.py": 0.73,
    "runtime/execute_composed.py": 0.94,
    "runtime/executor.py": 0.91,
    "runtime/freshness.py": 0.99,
    "runtime/identity.py": 0.99,
    "runtime/kernel.py": 0.96,
    "runtime/orchestration.py": 0.91,
    "runtime/provenance.py": 0.92,
    "runtime/provider_audit.py": 0.84,
    "runtime/provider_guard.py": 0.99,
    "server/__init__.py": 0.99,
    "server/mcp/__init__.py": 0.99,
    "server/mcp/__main__.py": 0.00,
    "server/mcp/_common.py": 0.99,
    "server/mcp/_server.py": 0.84,
    "server/mcp/_tools_impl.py": 0.99,
    "server/mcp/_tools_spec.py": 0.99,
    "server/runtime_http.py": 0.94,
    "server/runtime_ws.py": 0.85,
    "server/runtime_ws_server.py": 0.99,
    "server/serialization.py": 0.82,
    "server/wire_fields.py": 0.95,
    "sink/__init__.py": 0.99,
    "sink/local_day.py": 0.74,
    "sources/__init__.py": 0.89,
    "sources/_base_core.py": 0.92,
    "sources/_base_em.py": 0.90,
    "sources/_base_http.py": 0.86,
    "sources/_base_retry.py": 0.99,
    "sources/_corporate_common.py": 0.95,
    "sources/_corporate_financial.py": 0.83,
    "sources/_corporate_market.py": 0.90,
    "sources/_json_fetch.py": 0.84,
    "sources/_mob_fund.py": 0.99,
    "sources/_paginate.py": 0.90,
    "sources/_scale.py": 0.99,
    "sources/_session_astock.py": 0.89,
    "sources/_session_baidu.py": 0.99,
    "sources/_session_boards.py": 0.99,
    "sources/_session_datacenter.py": 0.85,
    "sources/_session_efinance.py": 0.99,
    "sources/_session_esg_chip.py": 0.99,
    "sources/_session_fund_v2.py": 0.99,
    "sources/_session_fundamental.py": 0.99,
    "sources/_session_holders.py": 0.99,
    "sources/_session_info.py": 0.99,
    "sources/_session_market.py": 0.99,
    "sources/_session_news.py": 0.99,
    "sources/_session_research.py": 0.96,
    "sources/_session_sector_macro.py": 0.99,
    "sources/adapters_fund.py": 0.85,
    "sources/astock_toolkit.py": 0.92,
    "sources/baidu/__init__.py": 0.99,
    "sources/baidu/adapters.py": 0.81,
    "sources/base.py": 0.99,
    "sources/boards.py": 0.75,
    "sources/boc/__init__.py": 0.99,
    "sources/boc/adapters.py": 0.99,
    "sources/chip.py": 0.79,
    "sources/cninfo/__init__.py": 0.99,
    "sources/cninfo/adapters.py": 0.95,
    "sources/corporate.py": 0.99,
    "sources/eastmoney/__init__.py": 0.99,
    "sources/eastmoney/adapters.py": 0.86,
    "sources/efinance_deriv.py": 0.70,
    "sources/efinance_fund.py": 0.86,
    "sources/efinance_options.py": 0.86,
    "sources/esg.py": 0.88,
    "sources/fin_report.py": 0.95,
    "sources/fund_company.py": 0.93,
    "sources/fund_manager.py": 0.95,
    "sources/fund_rank.py": 0.97,
    "sources/fundflow.py": 0.88,
    "sources/global_market.py": 0.82,
    "sources/governance.py": 0.99,
    "sources/hot_rank.py": 0.92,
    "sources/jsl/__init__.py": 0.99,
    "sources/jsl/adapters.py": 0.99,
    "sources/limits.py": 0.99,
    "sources/longhu.py": 0.99,
    "sources/market_stats.py": 0.96,
    "sources/news.py": 0.73,
    "sources/normalize.py": 0.93,
    "sources/session.py": 0.95,
    "sources/sina/__init__.py": 0.99,
    "sources/sina/adapters.py": 0.89,
    "sources/sina/fundflow.py": 0.89,
    "sources/sources.py": 0.94,
    "sources/tencent/__init__.py": 0.99,
    "sources/tencent/adapters.py": 0.91,
    "sources/ths/__init__.py": 0.99,
    "sources/ths/adapters.py": 0.72,
    "sources/ticks.py": 0.88,
    "sources/wallstreet/__init__.py": 0.99,
    "sources/wallstreet/adapters.py": 0.77,
    "sources/wencai.py": 0.97,
    "stream_contract.py": 0.96,
    "streaming/__init__.py": 0.99,
    "streaming/base.py": 0.83,
    "streaming/engine.py": 0.84,
    "streaming/push.py": 0.84,
    "streaming/state.py": 0.94,
    "streaming/stateful.py": 0.75,
    "tools/__init__.py": 0.72,
    "tools/_console.py": 0.99,
    "tools/_yaml_min.py": 0.75,
    "tools/capture.py": 0.82,
    "tools/check_originality.py": 0.72,
    "tools/codegen.py": 0.95,
    "tools/golden_audit.py": 0.94,
    "tools/golden_expand.py": 0.81,
    "tools/host_audit.py": 0.72,
    "tools/spec_audit.py": 0.83,
    "transport/__init__.py": 0.99,
    "transport/_validation.py": 0.94,
    "transport/async_.py": 0.90,
    "transport/base.py": 0.79,
    "transport/discover.py": 0.74,
    "transport/hosts.py": 0.89,
    "transport/policy.py": 0.96,
    "transport/pool.py": 0.80,
    "transport/ratelimit.py": 0.96,
    "transport/sniff.py": 0.76,
    "transport/speedtest.py": 0.89,
    "typed_query.py": 0.98,
    "universe/__init__.py": 0.90,
    "universe/_classes.py": 0.89,
    "universe/_scan.py": 0.99,
    "universe/_sources.py": 0.90,
}


def test_floor_table_keys_match_coverage_manifest() -> None:
    """地板表的键必须与 coverage.xml 的 filename 逐一对齐。

    这条判据是防"闸门空转"的：此前 FLOORS 的键被写成
    ``atst/cli/cli/parser.py`` / ``atst/sources.sina/sources/sina/adapters.py``
    这类与 coverage.xml（``source=["atst"]``，filename 为 ``cli/parser.py``）
    永不相交的形状，下面的回归判据对每个文件都 ``continue``，于是地板闸
    一条文件也没检查过。键名漂移必须在这里红，而不是静默跳过。
    """
    if not COVERAGE_XML.exists():
        pytest.skip("coverage.xml 不在场上（地板闸在 coverage 运行后生效）")
    tree = ET.parse(COVERAGE_XML)
    measured_files = {cls.get("filename") or "" for cls in tree.iter("class")}
    unknown = sorted(set(FLOORS) - measured_files)
    uncovered = sorted(measured_files - set(FLOORS))
    assert not unknown, "地板表存在 coverage.xml 中不存在的键：" + ", ".join(unknown[:20])
    assert not uncovered, "coverage.xml 中有文件没有地板：" + ", ".join(uncovered[:20])


def test_floor_table_has_no_strict_regression_vs_measured() -> None:
    """场上有 coverage.xml 时，逐文件实测率不得跌破地板。"""
    if not COVERAGE_XML.exists():
        pytest.skip("coverage.xml 不在场上（地板闸在 coverage 运行后生效）")
    tree = ET.parse(COVERAGE_XML)
    regressions: list[str] = []
    for cls in tree.iter("class"):
        fn = cls.get("filename") or ""
        assert fn in FLOORS, f"coverage.xml 文件 {fn!r} 没有地板，闸门会空转"
        measured = float(cls.get("line-rate") or 0.0)
        if measured + 1e-9 < FLOORS[fn]:
            regressions.append(f"{fn}: measured={measured:.2f} < floor={FLOORS[fn]:.2f}")
    assert not regressions, "覆盖率跌破地板：" + "; ".join(regressions)
