# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

import json
from typing import Any

import pytest

from tstdx.cli.parser import build_parser
from tstdx.integration.mcp._tools_impl import _h_query_capability
from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult


class _FakeClient:
    def __init__(self) -> None:
        plan = QueryPlanner().compile(QuerySpec.build("rates", provider="boc"))
        self.result = QueryResult.from_plan(
            [{"currency": "USD", "cash_buy": 700.0}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return ("rates", "balance_sheet")

    def call(self, capability: str, *args: Any, **kwargs: Any) -> QueryResult[Any]:
        self.calls.append((capability, args, kwargs))
        return self.result


def test_ws_query_delegates_to_client_call() -> None:
    fake = _FakeClient()
    handler = RuntimeJsonRpcHandler(fake)  # type: ignore[arg-type]
    raw = handler.handle_message(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "query",
                "params": {
                    "capability": "balance_sheet",
                    "provider": "derived",
                    "args": ["sh600519"],
                    "kwargs": {"size": 5},
                },
            }
        )
    )
    assert raw is not None
    payload = json.loads(raw)
    assert payload["id"] == 7
    assert "result" in payload
    assert fake.calls == [
        (
            "balance_sheet",
            ("sh600519",),
            {
                "provider": "derived",
                "channel": None,
                "currentness": "business",
                "size": 5,
            },
        )
    ]


def test_mcp_query_capability_delegates_to_client_call() -> None:
    fake = _FakeClient()
    payload = _h_query_capability(
        fake,  # type: ignore[arg-type]
        {
            "capability": "balance_sheet",
            "provider": "derived",
            "args": ["sh600519"],
            "kwargs": {"size": 5},
        },
    )
    assert payload["meta"]["provider"] == "boc"
    assert fake.calls[0][0] == "balance_sheet"
    assert fake.calls[0][1] == ("sh600519",)
    assert fake.calls[0][2]["size"] == 5


def test_cli_parser_exposes_generic_query_and_discovery() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "query",
            "balance_sheet",
            "--provider",
            "derived",
            "--args",
            '["sh600519"]',
            "--kwargs",
            '{"size":5}',
        ]
    )
    assert args.command == "query"
    assert args.capability == "balance_sheet"
    assert json.loads(args.args_json) == ["sh600519"]
    assert json.loads(args.kwargs_json) == {"size": 5}

    discovery = parser.parse_args(["capabilities"])
    assert discovery.command == "capabilities"


def test_http_query_and_capability_discovery_delegate_to_client() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tstdx.integration.runtime_http import create_runtime_app

    fake = _FakeClient()
    app = create_runtime_app(fake)  # type: ignore[arg-type]
    with TestClient(app) as test_client:
        discovery = test_client.get("/v13/capabilities")
        assert discovery.status_code == 200
        assert discovery.json()["capabilities"] == ["rates", "balance_sheet"]

        response = test_client.post(
            "/v13/query/balance_sheet",
            json={
                "provider": "derived",
                "args": ["sh600519"],
                "kwargs": {"size": 5},
            },
        )
    assert response.status_code == 200
    assert response.json()["meta"]["provider"] == "boc"
    assert fake.calls[-1][0] == "balance_sheet"
    assert fake.calls[-1][1] == ("sh600519",)
    assert fake.calls[-1][2]["size"] == 5
