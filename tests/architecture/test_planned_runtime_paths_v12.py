from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tstdx
from tstdx.async_service import UnifiedMarketDataService as AsyncSyncService
from tstdx.batch import BatchResult
from tstdx.client_api import Client
from tstdx.facade import AsyncUnifiedQuoteAPI, UnifiedQuoteAPI
from tstdx.failure import FailureDisposition
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.planned_service import UnifiedMarketDataService as PlannedService
from tstdx.providers import PROVIDERS
from tstdx.query import QueryPlan, QueryPlanner, QuerySpec

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "tstdx"

#: v12 integration surfaces deleted in the v15 clean-break (see .gitignore and
#: ``docs/REFACTOR_PLAN_v15_CONSOLIDATION.md`` Phase 2). Their canonical
#: replacements are ``runtime_http`` / ``runtime_ws`` / ``runtime_ws_server`` /
#: ``runtime_tasks`` / ``mcp_server``.
DELETED_V12_SURFACES = {
    "http_server",
    "http_app",
    "http_runtime",
    "ws_server",
    "ws_app",
    "tasks",
    "mcp_app",
}

#: The deleted module files themselves still exist on disk (git-ignored); they
#: are excluded from the guard below because they legitimately import each other.
_DELETED_FILES = {PKG / "integration" / f"{name}.py" for name in DELETED_V12_SURFACES}


def test_top_level_contracts_point_to_canonical_modules() -> None:
    """顶层惰性导出必须指向 v15 canonical 模块（单一事实源）。"""
    assert tstdx._LAZY["Client"] == ("tstdx.client_api", "Client")
    assert tstdx._LAZY["QuerySpec"] == ("tstdx.query", "QuerySpec")
    assert tstdx._LAZY["QueryPlan"] == ("tstdx.query", "QueryPlan")
    assert tstdx._LAZY["QueryPlanner"] == ("tstdx.query", "QueryPlanner")
    assert tstdx._LAZY["PROVIDERS"] == ("tstdx.providers", "PROVIDERS")
    assert tstdx._LAZY["UnifiedRuntime"] == ("tstdx.runtime_v13", "UnifiedRuntime")
    assert tstdx._LAZY["BatchResult"] == ("tstdx.batch", "BatchResult")
    assert tstdx._LAZY["StreamState"] == ("tstdx.streaming.state", "StreamState")

    assert tstdx.Client is Client
    assert tstdx.PROVIDERS is PROVIDERS
    assert tstdx.BatchResult is BatchResult
    assert tstdx.QuerySpec is QuerySpec
    assert tstdx.QueryPlan is QueryPlan
    assert tstdx.QueryPlanner is QueryPlanner


def test_facade_classes_live_in_their_canonical_modules() -> None:
    """官方门面必须导出 Provider-bound 的 planned / strict_async 实现。

    历史三通路实现只能通过 ``LegacyUnifiedQuoteAPI`` 别名访问
    （``tstdx.facade.api``），不得占据 ``UnifiedQuoteAPI`` 位置。
    """
    assert UnifiedMarketDataService is PlannedService
    assert UnifiedQuoteAPI.__module__ == "tstdx.facade.planned"
    assert AsyncUnifiedQuoteAPI.__module__ == "tstdx.facade.strict_async"


def test_async_market_data_uses_same_planned_sync_service() -> None:
    assert AsyncSyncService is PlannedService


def test_http_boundary_is_the_canonical_runtime_http() -> None:
    from tstdx.integration import runtime_http

    assert runtime_http.__all__ == ["create_runtime_app"]
    app = runtime_http.create_runtime_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v13/runtime/health" in paths
    assert "/v13/quotes" in paths


def test_ws_and_mcp_boundaries_are_the_canonical_modules() -> None:
    from tstdx.integration import mcp_server, runtime_ws, runtime_ws_server

    assert runtime_ws.__all__ == ["RuntimeJsonRpcHandler"]
    assert set(runtime_ws_server.__all__) == {"RuntimeWsConfig", "serve_runtime_ws"}
    assert {"MCPServer", "create_mcp_server"} <= set(mcp_server.__all__)

    server = mcp_server.create_mcp_server()
    try:
        assert isinstance(server, mcp_server.MCPServer)
    finally:
        server.stop()


def test_no_tracked_module_imports_deleted_v12_surfaces() -> None:
    """v15 clean-break 后，canonical 代码不得再导入已删除的 v12 集成面。"""
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        if path in _DELETED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                leaf = node.module.rsplit(".", 1)[-1]
                if leaf in DELETED_V12_SURFACES and "integration" in str(node.module):
                    offenders.append(f"{path.relative_to(ROOT)} -> {node.module}")
    assert offenders == []


def test_failure_disposition_cannot_be_constructed_with_provider_switch() -> None:
    with pytest.raises(TypeError):
        FailureDisposition(
            retry_same_provider=False,
            switch_host=False,
            retry_after=None,
            terminal=True,
            reason="test",
            provider_switch_allowed=True,
        )
