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
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    @staticmethod
    def _result(capability: str, provider: str) -> QueryResult[Any]:
        plan = QueryPlanner().compile(
            QuerySpec.build(
                capability,
                symbols="sh600519",
                provider=provider,
                currentness="live",
            )
        )
        return QueryResult.from_plan(
            [{"code": "sh600519", "provider": provider}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def minute(
        self, symbol: str, *, provider: str | None = None, currentness: str = "live"
    ) -> QueryResult[Any]:
        del symbol, currentness
        self.calls.append(("minute", provider))
        assert provider is not None
        return self._result("minute", provider)

    def trades(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        start: int = 0,
        count: int = 0,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        del symbol, start, count, currentness
        self.calls.append(("trades", provider))
        assert provider is not None
        return self._result("trades", provider)


def _client() -> tuple[Client, _RecordingRuntime]:
    runtime = _RecordingRuntime()
    return Client(runtime=runtime), runtime  # type: ignore[arg-type]


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
    client, runtime = _client()

    client.minute("sh600519")
    client.trades("sh600519")

    assert runtime.calls == [("minute", "tencent"), ("trades", "tencent")]


def test_cli_minute_and_trades_do_not_force_tdx() -> None:
    parser = build_parser()

    assert parser.parse_args(["minute", "sh600519"]).provider is None
    assert parser.parse_args(["trades", "sh600519"]).provider is None
    assert parser.parse_args(["snapshot", "sh600519"]).provider == "tdx"
    assert parser.parse_args(["security-list"]).provider == "tdx"


def test_mcp_minute_and_trades_reach_client_operational_default() -> None:
    client, runtime = _client()

    minute = _h_get_minute_today(client, {"symbol": "sh600519"})
    trades = _h_get_trades(client, {"symbol": "sh600519"})

    assert minute["meta"]["provider"] == "tencent"
    assert trades["meta"]["provider"] == "tencent"
    assert runtime.calls == [("minute", "tencent"), ("trades", "tencent")]


def test_ws_minute_and_trades_reach_client_operational_default() -> None:
    client, runtime = _client()
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

    assert runtime.calls == [("minute", "tencent"), ("trades", "tencent")]


def test_http_minute_and_trades_reach_client_operational_default() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from atst.integration.runtime_http import create_runtime_app

    client, runtime = _client()
    app = create_runtime_app(client)
    with TestClient(app) as http:
        minute = http.get("/v13/minute/sh600519")
        trades = http.get("/v13/trades/sh600519")

    assert minute.status_code == 200
    assert trades.status_code == 200
    assert minute.json()["meta"]["provider"] == "tencent"
    assert trades.json()["meta"]["provider"] == "tencent"
    assert runtime.calls == [("minute", "tencent"), ("trades", "tencent")]


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
