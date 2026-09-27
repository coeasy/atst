# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from atst.cli.parser import build_parser
from atst.integration.mcp._tools_impl import _h_query_capability
from atst.integration.mcp._tools_spec import TOOLS
from atst.integration.runtime_ws import RuntimeJsonRpcHandler
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult

_ROOT = Path(__file__).resolve().parents[2]


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

    from atst.integration.runtime_http import create_runtime_app

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


def test_http_route_parameters_all_reach_the_execution_path() -> None:
    """HTTP 路由形参 = 对外可见的查询参数；收下却不用就是门面版的 ``max_age``。

    同一条判据已在 CLI（F-27/F-28）、``QuerySpec`` 字段（F-43）与 ``options`` 袋
    （F-46）上各自成立，路由签名是它在 HTTP 面上的对应物。
    """
    tree = _source_ast("atst/integration/runtime_http.py")
    routes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in {"get", "post"}
            for dec in node.decorator_list
        )
    ]
    assert len(routes) >= 8, f"只扫到 {len(routes)} 条 HTTP 路由，说明扫描自身失效"
    for route in routes:
        loaded = {
            name.id
            for name in ast.walk(route)
            if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Load)
        }
        declared = [arg.arg for arg in [*route.args.args, *route.args.kwonlyargs]]
        unused = sorted(set(declared) - loaded - {route.name})
        assert unused == [], f"{route.name}() 的查询参数没有进入执行路径（幻影开关）：{unused}"


def test_mcp_input_schema_and_handler_agree_in_both_directions() -> None:
    """``inputSchema`` 是对外契约的全部：多一个键是幻影开关，少一个键是未声明输入。"""
    tree = _source_ast("atst/integration/mcp/_tools_impl.py")
    handlers = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert len(TOOLS) >= 5, "MCP 工具清单为空，说明门禁自身失效"
    for tool in TOOLS:
        handler = handlers.get(tool.handler.__name__)
        assert handler is not None, f"{tool.name} 的 handler 不在 _tools_impl.py，判据需同步更新"
        declared = set(tool.inputSchema.get("properties", {}))
        read = _bag_keys(handler)
        assert declared, f"{tool.name} 未声明任何 inputSchema 属性，说明清单读空了"
        assert declared == read, (
            f"{tool.name}: 声明却无人读取 {sorted(declared - read)}；"
            f"读取却未声明 {sorted(read - declared)}"
        )


def test_ws_declared_methods_are_all_dispatched() -> None:
    """JSON-RPC 的 ``METHODS`` 名单与实际分派分支必须一一对应，两个方向都不许有差。"""
    tree = _source_ast("atst/integration/runtime_ws.py")
    dispatched: set[str] = set()
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Compare)
            or not isinstance(node.left, ast.Name)
            or node.left.id != "method"
        ):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                dispatched.add(comparator.value)
            elif isinstance(comparator, (ast.Set, ast.Tuple, ast.List)):
                dispatched |= {
                    item.value
                    for item in comparator.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
    declared = set(RuntimeJsonRpcHandler.METHODS)
    assert len(dispatched) >= 5, f"只扫到 {len(dispatched)} 个分派分支，说明扫描自身失效"
    assert declared == dispatched, (
        f"声明却未分派 {sorted(declared - dispatched)}；分派却未声明 {sorted(dispatched - declared)}"
    )


def _source_ast(relative: str) -> ast.Module:
    return ast.parse((_ROOT / relative).read_text(encoding="utf-8"))


def _bag_keys(handler: ast.FunctionDef) -> set[str]:
    """String keys the handler reads out of its ``args`` mapping."""

    bag = handler.args.args[1].arg
    keys: set[str] = set()
    for node in ast.walk(handler):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == bag
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == bag
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys
