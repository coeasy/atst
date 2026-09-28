# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

from __future__ import annotations

import json
from typing import Any

import pytest

from atst.client.api import Client
from atst.cli.parser import build_parser
from atst.integration.mcp._tools_impl import _h_get_minute_today, _h_get_trades
from atst.integration.runtime_ws import RuntimeJsonRpcHandler
from atst.providers import PROVIDERS, resolve_capability_provider
from atst.query import QueryPlan, QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.runtime.kernel import UnifiedRuntime


class _RecordingExecutor:
    def __init__(self) -> None:
        self.plans: list[QueryPlan] = []

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        self.plans.append(plan)
        return QueryResult.from_plan(
            [{"code": "sh600519", "provider": plan.provider}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )


def _client() -> tuple[Client, _RecordingExecutor]:
    executor = _RecordingExecutor()
    runtime = UnifiedRuntime(executor=executor)
    return Client(runtime=runtime), executor


def test_registry_separates_declared_from_operational_support() -> None:
    tdx = PROVIDERS.get("tdx")

    assert tdx.supports("minute")
    assert tdx.supports("trades")
    assert tdx.supports("security_list")
    assert not tdx.operationally_supports("minute")
    assert not tdx.operationally_supports("trades")
    assert not tdx.operationally_supports("security_list")

    assert PROVIDERS.default_available_provider("minute") == "tencent"
    assert PROVIDERS.default_available_provider("trades") == "tencent"
    assert PROVIDERS.default_available_provider("security_list") is None


def test_explicit_provider_is_never_replaced_by_operational_default() -> None:
    assert resolve_capability_provider("minute", "tdx") == "tdx"
    assert resolve_capability_provider("trades", "baidu") == "baidu"


def test_client_minute_and_trades_have_working_omitted_provider_defaults() -> None:
    client, executor = _client()

    client.minute("sh600519")
    client.trades("sh600519")

    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
    ]


def test_cli_minute_and_trades_do_not_force_tdx() -> None:
    parser = build_parser()

    assert parser.parse_args(["minute", "sh600519"]).provider is None
    assert parser.parse_args(["trades", "sh600519"]).provider is None
    assert parser.parse_args(["snapshot", "sh600519"]).provider == "tdx"
    assert parser.parse_args(["security-list"]).provider == "tdx"


def test_mcp_minute_and_trades_reach_client_operational_default() -> None:
    client, executor = _client()

    minute = _h_get_minute_today(client, {"symbol": "sh600519"})
    trades = _h_get_trades(client, {"symbol": "sh600519"})

    assert minute["meta"]["provider"] == "tencent"
    assert trades["meta"]["provider"] == "tencent"
    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
    ]


def test_ws_minute_and_trades_reach_client_operational_default() -> None:
    client, executor = _client()
    handler = RuntimeJsonRpcHandler(client)

    for request_id, method in ((1, "minute"), (2, "trades")):
        raw = handler.handle_message(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": {"symbol": "sh600519"},
                }
            )
        )
        assert raw is not None
        payload = json.loads(raw)
        assert payload["result"]["meta"]["provider"] == "tencent"

    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
    ]


def test_http_minute_and_trades_reach_client_operational_default() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from atst.integration.runtime_http import create_runtime_app

    client, executor = _client()
    app = create_runtime_app(client)
    with TestClient(app) as http:
        minute = http.get("/v13/minute/sh600519")
        trades = http.get("/v13/trades/sh600519")

    assert minute.status_code == 200
    assert trades.status_code == 200
    assert minute.json()["meta"]["provider"] == "tencent"
    assert trades.json()["meta"]["provider"] == "tencent"
    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
    ]


def test_core_capability_discovery_distinguishes_declared_and_available() -> None:
    core = Client.core_capability_statuses()

    assert core["minute"]["available"] is True
    assert core["minute"]["default_provider"] == "tencent"
    assert "tdx" in core["minute"]["declared_providers"]
    assert "tdx" not in core["minute"]["operational_providers"]

    assert core["trades"]["available"] is True
    assert core["trades"]["default_provider"] == "tencent"

    assert core["security_list"]["available"] is False
    assert core["security_list"]["default_provider"] is None
    assert core["security_list"]["operational_providers"] == []

    for capability in ("quotes", "bars", "snapshot", "security_count"):
        assert core[capability]["available"] is True
        assert core[capability]["default_provider"] == "tdx"


def test_unified_runtime_and_generic_query_share_operational_defaults() -> None:
    executor = _RecordingExecutor()
    runtime = UnifiedRuntime(executor=executor)

    runtime.minute("sh600519")
    runtime.trades("sh600519")
    runtime.execute(
        QuerySpec.build("minute", symbols="sh600519", currentness="live")
    )

    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
        ("minute", "tencent"),
    ]


def test_planner_only_replaces_omitted_unavailable_default() -> None:
    default_tdx = QueryPlanner(default_provider="tdx")

    minute = default_tdx.compile(
        QuerySpec.build("minute", symbols="sh600519", currentness="live")
    )
    explicit_tdx = default_tdx.compile(
        QuerySpec.build("minute", symbols="sh600519", provider="tdx", currentness="live")
    )
    security_list = default_tdx.compile(
        QuerySpec.build(
            "security_list",
            provider=None,
            currentness="business",
            options={"market": 0},
        )
    )

    assert minute.provider == "tencent"
    assert explicit_tdx.provider == "tdx"
    assert security_list.provider == "tdx"


def test_generic_client_call_uses_same_core_operational_default() -> None:
    client, executor = _client()

    minute = client.call("minute", "sh600519")
    trades = client.call("trades", "sh600519")

    assert minute.meta.provider == "tencent"
    assert trades.meta.provider == "tencent"
    assert [(plan.spec.capability, plan.provider) for plan in executor.plans] == [
        ("minute", "tencent"),
        ("trades", "tencent"),
    ]


def test_http_health_reports_unavailable_core_capabilities() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from atst.integration.runtime_http import create_runtime_app

    client, _executor = _client()
    with TestClient(create_runtime_app(client)) as http:
        response = http.get("/v13/runtime/health")

    assert response.status_code == 200
    assert "security_list" in response.json()["core_unavailable"]
    assert "minute" not in response.json()["core_unavailable"]
    assert "trades" not in response.json()["core_unavailable"]


def test_ws_health_reports_unavailable_core_capabilities() -> None:
    client, _executor = _client()
    handler = RuntimeJsonRpcHandler(client)
    raw = handler.handle_message(
        json.dumps({"jsonrpc": "2.0", "id": 9, "method": "runtime.health", "params": {}})
    )

    assert raw is not None
    payload = json.loads(raw)["result"]
    assert "security_list" in payload["core_unavailable"]
    assert "minute" not in payload["core_unavailable"]
    assert "trades" not in payload["core_unavailable"]


def test_core_capability_status_uses_effective_runtime_default() -> None:
    core = Client.core_capability_statuses(default_provider="eastmoney")

    assert core["minute"]["available"] is True
    assert core["minute"]["default_provider"] == "eastmoney"
    assert core["trades"]["available"] is True
    assert core["trades"]["default_provider"] == "tencent"
    assert core["security_list"]["available"] is False
    assert core["security_list"]["default_provider"] is None
