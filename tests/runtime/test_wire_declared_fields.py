# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""三张 wire 面对"未声明的请求字段"必须当场拒绝，而不是收下后无人读（F-47 裁决 (a)）。

F-43/F-46 把 ``max_age``/``allow_partial`` 从构造面清掉之后，同一个旋钮在四个入口上的下场并不
一致（第 23 步实测）：``Client.bars(..., max_age=0)`` 与 ``QuerySpec.build(..., max_age=0)``
都 ``TypeError``，而 ``GET /v13/bars/600519?max_age=0`` 返回 **200 + 正常结果**，
``POST /v13/query/...`` 的 body 键、WS 的 ``params``、MCP 的 ``arguments`` 同样蒸发。删掉的
旋钮在 wire 面上重新变成"看起来生效"——本步的授权范围就是把这三面钉成和构造面同一个口径。

判据分两类，缺一类都不算数：

**推导类**（白名单是不是真源）
    名单与分派代码的读取点求差：WS 的 :data:`WS_PARAMS_FIELDS` 对 ``_dispatch``/``_policy``，
    HTTP body 的 :data:`QUERY_BODY_FIELDS` 对 ``query_capability`` 里的 ``payload.get(...)``；
    MCP 直接以对外声明的那份 ``inputSchema`` 为真源，所以"声明"与"拒绝"不可能各说一套。
    再加一条反抄件保险：整个包里只许 ``wire_fields`` 定义那两份名单，四面各自必须真的调用
    唯一拒绝口。

**行为类**（每一面都真打一遍）
    每条 HTTP 路由、每个 WS 方法、每个 MCP 工具：带全部已声明字段 → 不许被拒；带一个未知
    字段 → 必须被拒，且人读的那句话里点着这个键、机读侧的 ``unknown_fields`` 也点着它。

两侧分开断言是第 38 步的教训：``TdxError.__str__`` 会把 ``context`` 拼进字符串，只断言
"消息里有 max_age"可能只是机读侧在替人读侧作证。
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path
from typing import Any, get_args

import pytest

from tstdx.integration.mcp._common import ERR_INVALID_PARAMS
from tstdx.integration.mcp._server import MCPServer
from tstdx.integration.mcp._tools_spec import TOOLS
from tstdx.integration.runtime_http import create_runtime_app
from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler
from tstdx.integration.wire_fields import QUERY_BODY_FIELDS, WS_PARAMS_FIELDS
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult

_ROOT = Path(__file__).resolve().parents[2]
#: 本步的探针就是那个刚被删掉的旋钮：它在构造面已经会 ``TypeError``，wire 面必须同口径。
UNKNOWN = "max_age"
HTTP = "tstdx/integration/runtime_http.py"


def _tree_of(obj: Any) -> ast.Module:
    """一个可调用对象的源码 AST（按运行时真身解析，不复制第二份结构知识）。"""
    return ast.parse(textwrap.dedent(inspect.getsource(obj)))


def _bag_keys(func: Any, bag: str) -> set[str]:
    """函数体里从某个映射袋读出的字符串键（``bag.get("k")`` 与 ``bag["k"]``）。"""
    keys: set[str] = set()
    for node in ast.walk(_tree_of(func)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == bag
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
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


def _rejects_called(func: Any) -> bool:
    return any(
        isinstance(node, ast.Call) and getattr(node.func, "id", "") == "reject_undeclared"
        for node in ast.walk(_tree_of(func))
    )


class _Recorder:
    """替 ``Client`` 记账：每次调用录下方法名、位置参数与关键字参数。"""

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

    @property
    def runtime(self) -> Any:
        return type("R", (), {"planner": type("P", (), {"default_provider": "tdx"})()})()

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> QueryResult[Any]:
            self.calls.append((name, args, kwargs))
            return self.result

        return _record


def _app(client: Any) -> Any:
    pytest.importorskip("fastapi")
    return create_runtime_app(client)  # type: ignore[arg-type]


def _test_client(client: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(_app(client), raise_server_exceptions=False)


def _route(app: Any, path: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", "") == path:
            return route
    raise AssertionError(f"找不到路由 {path}，判据自身失效")


# --------------------------------------------------------------------------
# 推导类：白名单 = 读取点，两个方向都不许有差
# --------------------------------------------------------------------------


def test_ws_whitelist_covers_exactly_the_dispatched_methods() -> None:
    """WS 的字段表按方法给，方法集合必须与 ``METHODS`` 双向相等。"""
    declared = set(WS_PARAMS_FIELDS)
    methods = set(RuntimeJsonRpcHandler.METHODS)
    assert len(methods) >= 8, f"只解析出 {len(methods)} 个 WS 方法，判据自身失效"
    assert declared == methods, (
        f"表里有却分派不到 {sorted(declared - methods)}；分派了却没表 {sorted(methods - declared)}"
        "（没有表的方法会 KeyError，等于把未知字段问题换成整条方法不可用）"
    )


def test_ws_whitelist_is_the_keys_the_dispatch_reads() -> None:
    """字段表的并集 ↔ ``_dispatch`` + ``_policy`` 的读取点，两个方向求差。"""
    read = _bag_keys(RuntimeJsonRpcHandler._dispatch, "params") | _bag_keys(
        RuntimeJsonRpcHandler._policy, "params"
    )
    declared = set().union(*WS_PARAMS_FIELDS.values())
    assert read, "扫不出 WS 分派读过任何键，说明扫描自身失效"
    phantom = sorted(declared - read)
    unused = sorted(read - declared)
    assert phantom == [], f"这些 WS 字段声明了却无人读取（wire 面版的 max_age）：{phantom}"
    assert unused == [], f"这些 WS 字段被读取却不在任何方法的表里（会被自己的闸误杀）：{unused}"


def test_http_body_whitelist_is_the_keys_the_route_reads() -> None:
    """``QUERY_BODY_FIELDS`` 必须与 ``query_capability`` 真正读走的 body 键双向相等。"""
    endpoint = _route(_app(_Recorder()), "/v13/query/{capability}").endpoint
    read = _bag_keys(endpoint, "payload")
    assert read, "扫不出该路由读过任何 body 键，说明扫描自身失效"
    assert frozenset(read) == QUERY_BODY_FIELDS, (
        f"HTTP body 名单与路由读取点不一致：声明却无人读 {sorted(QUERY_BODY_FIELDS - read)}；"
        f"读取却未声明 {sorted(read - QUERY_BODY_FIELDS)}"
    )


def test_mcp_schemas_declare_that_they_reject_undeclared_arguments() -> None:
    """``additionalProperties: false`` 必须在每一张对外 schema 里——它同时就是拒绝用的名单。"""
    assert len(TOOLS) >= 5, "MCP 工具清单读空了，判据自身失效"
    missing = sorted(
        tool.name for tool in TOOLS if tool.inputSchema.get("additionalProperties") is not False
    )
    assert missing == [], (
        f"这些工具的 schema 允许任意键，模型递进来的未知字段会被静默收下：{missing}"
    )


def test_every_wire_face_rejects_through_the_one_refusal_point() -> None:
    """四面各自必须真的调用 ``reject_undeclared``，否则名单只是一份没人读的注释。"""
    app = _app(_Recorder())
    faces = {
        "HTTP 查询串": _rejects_called(create_runtime_app),
        "HTTP body": _rejects_called(_route(app, "/v13/query/{capability}").endpoint),
        "WS params": _rejects_called(RuntimeJsonRpcHandler._dispatch),
        "MCP arguments": _rejects_called(MCPServer._handle_tools_call),
    }
    assert all(faces.values()), f"这些面没有接上拒绝口，未知字段仍然会静默蒸发：{faces}"


def test_the_wire_field_lists_have_exactly_one_definition_site() -> None:
    """两份名单只许在 ``wire_fields`` 里定义一次：抄一份就会过期（F-42 口径）。"""
    names = ("QUERY_BODY_FIELDS", "WS_PARAMS_FIELDS")
    owners: dict[str, list[str]] = {name: [] for name in names}
    scanned = 0
    for path in (_ROOT / "tstdx").rglob("*.py"):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in owners:
                    owners[target.id].append(str(path.relative_to(_ROOT)))
    assert scanned >= 150, f"只扫到 {scanned} 个模块，判据自身失效"
    expected = str(Path("tstdx") / "integration" / "wire_fields.py")
    copied = {name: sites for name, sites in owners.items() if sites != [expected]}
    assert copied == {}, f"这些名单出现了第二份定义点：{copied}"


# --------------------------------------------------------------------------
# 行为类：三面各打一遍
# --------------------------------------------------------------------------


def _decorated_paths(verb: str) -> list[str]:
    """HTTP 路由清单从源码的装饰器里扫，不手抄。"""
    tree = ast.parse((_ROOT / HTTP).read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == verb
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                found.append(str(dec.args[0].value))
    return found


_GET_ROUTES = _decorated_paths("get")
_REQUESTS = [("get", path) for path in _GET_ROUTES] + [
    ("post", path) for path in _decorated_paths("post")
]


def _fill(path: str) -> str:
    return path.replace("{symbol}", "600519").replace("{capability}", "rates")


def _query_values(app: Any, path: str) -> dict[str, str]:
    """已声明查询参数的样本值，按注记类型现推（int → ``1``，其余 → ``tdx``）。"""
    values: dict[str, str] = {}
    for param in _route(app, path).dependant.query_params:
        annotation = param.field_info.annotation
        values[param.name] = "1" if annotation is int or int in get_args(annotation) else "tdx"
    return values


def test_route_list_is_read_from_the_source_not_a_copy() -> None:
    assert len(_REQUESTS) >= 10, f"只扫到 {len(_REQUESTS)} 条 HTTP 路由，判据自身失效"
    assert any("{symbol}" in path for _, path in _REQUESTS), "扫不到带路径参数的路由，用例会架空"


@pytest.mark.parametrize("path", _GET_ROUTES)
def test_http_get_route_answers_when_only_declared_fields_are_sent(path: str) -> None:
    """带满已声明字段的请求必须仍然通——fail-closed 的第一代价是别把自己人挡在门外。

    这条不是冗余：本步最初的实现让**所有**请求都 422（FastAPI 把闭包里注记为 ``Request``
    的形参读成必填查询参数），只有真打一遍才看得见。
    """
    recorder = _Recorder()
    app = _app(recorder)
    with _test_client(recorder) as test_client:
        response = test_client.get(_fill(path), params=_query_values(app, path))
    assert response.status_code == 200, f"{path} 带已声明字段却被拒：{response.text[:200]}"


@pytest.mark.parametrize(("verb", "path"), _REQUESTS)
def test_http_route_rejects_an_undeclared_query_field(verb: str, path: str) -> None:
    recorder = _Recorder()
    with _test_client(recorder) as test_client:
        response = test_client.request(
            verb.upper(), _fill(path), params={UNKNOWN: "0"}, json={"args": []}
        )
    assert response.status_code == 422, f"{path} 上的未知查询字段仍然被静默收下"
    body = response.json()["error"]
    assert UNKNOWN in body["message"], f"{path}: 人读的那句话没点出被拒的键"
    assert body["context"]["unknown_fields"] == [UNKNOWN], f"{path}: 机读侧没给出 unknown_fields"


def test_http_query_body_rejects_an_undeclared_field() -> None:
    recorder = _Recorder()
    with _test_client(recorder) as test_client:
        ok = test_client.post("/v13/query/rates", json={"args": ["600519"], "provider": "tdx"})
        bad = test_client.post("/v13/query/rates", json={"args": ["600519"], UNKNOWN: 0})
    assert ok.status_code == 200, ok.text[:200]
    assert recorder.calls[-1][2]["provider"] == "tdx"
    assert bad.status_code == 422, "未知 body 键仍然被静默收下"
    body = bad.json()["error"]
    assert UNKNOWN in body["message"] and body["context"]["unknown_fields"] == [UNKNOWN]


def _ws_sample(key: str) -> Any:
    """按字段名给一个能过后续校验的值；表里冒出没有样本的新键时，测试自己先红。"""
    samples: dict[str, Any] = {
        "symbol": "600519",
        "symbols": ["600519"],
        "provider": "tdx",
        "fallback": "tdx",
        "period": "day",
        "count": 1,
        "start": 0,
        "adjustment": "",
        "market": "0",
        "capability": "rates",
        "args": [],
        "kwargs": {},
        "channel": "quotation",
        "currentness": "business",
    }
    assert key in samples, f"WS 字段表里的 {key} 没有测试样本，判据会被架空"
    return samples[key]


def _ws_reply(method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = RuntimeJsonRpcHandler(_Recorder()).handle_message(  # type: ignore[arg-type]
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    assert raw is not None
    return json.loads(raw)


@pytest.mark.parametrize("method", sorted(RuntimeJsonRpcHandler.METHODS))
def test_ws_method_rejects_and_accepts_by_the_table(method: str) -> None:
    """每个方法：带满表里的键要通，多一个未知键必须 -32602，且人读机读两侧都点出它。"""
    declared = WS_PARAMS_FIELDS[method]
    filled = {key: _ws_sample(key) for key in sorted(declared)}
    accepted = _ws_reply(method, filled)
    assert "error" not in accepted, f"WS {method} 带已声明字段却被拒：{accepted}"
    rejected = _ws_reply(method, {**filled, UNKNOWN: 0})
    error = rejected.get("error")
    assert error is not None, f"WS {method} 的未知 params 键仍然静默蒸发：{rejected}"
    assert error["code"] == ERR_INVALID_PARAMS
    # 两张 JSON-RPC 面把同一句话放在不同位置（实测）：WS 的 ``error.message`` 按规范是通用的
    # "invalid params"，理由在 ``error.data``；MCP 直接把理由平铺进 ``error.message``。
    # 因此本判据各按自己那面真实公开的读法断言，而不是假设两面同形。
    assert UNKNOWN in error["data"]["message"], (
        f"WS {method}: 人读的那句话没点出被拒的键（{error['data']['message']}）"
    )
    assert error["data"]["context"]["unknown_fields"] == [UNKNOWN]


def _mcp_sample(spec: dict[str, Any]) -> Any:
    kind = spec.get("type")
    if kind == "integer":
        return 1
    if kind == "array":
        return ["600519"] if spec.get("items") == {"type": "string"} else []
    if kind == "object":
        return {}
    return "tdx"


@pytest.mark.parametrize(
    "tool", sorted(TOOLS, key=lambda spec: spec.name), ids=lambda spec: spec.name
)
def test_mcp_tool_rejects_and_accepts_by_its_schema(tool: Any) -> None:
    """MCP：schema 就是白名单——声明的属性递进去要通，多一个键必须 -32602。"""
    server = MCPServer(_Recorder())  # type: ignore[arg-type]
    properties = tool.inputSchema["properties"]
    assert properties, f"{tool.name} 没有声明任何属性，判据会被架空"
    payload = {key: _mcp_sample(spec) for key, spec in properties.items()}

    def _call(arguments: dict[str, Any]) -> dict[str, Any]:
        reply = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool.name, "arguments": arguments},
            }
        )
        assert reply is not None
        return reply

    accepted = _call(payload)
    assert "error" not in accepted, f"{tool.name} 带已声明属性却被拒：{accepted}"
    rejected = _call({**payload, UNKNOWN: 0})
    error = rejected.get("error")
    assert error is not None, f"{tool.name} 的未知 arguments 键仍然静默蒸发：{rejected}"
    assert error["code"] == ERR_INVALID_PARAMS
    assert UNKNOWN in error["message"]
    assert error["data"]["context"]["unknown_fields"] == [UNKNOWN]


# --------------------------------------------------------------------------
# 行为类：路由字段不许混进 kwargs（第 29 轮）
# --------------------------------------------------------------------------


def test_every_wire_face_refuses_routing_fields_smuggled_inside_kwargs() -> None:
    """``provider`` / ``channel`` / ``currentness`` 只有顶层声明才有决策权。

    三张 query 面都写成 ``client.call(cap, *args, provider=<顶层值>, ..., **kwargs)``。
    同一个键两处都出现时，Python 在**调用表达式求值处**就抛裸 ``TypeError: got multiple
    values for keyword argument``——发生在进入 ``Client.call`` 之前，那层把入参不合签名翻成
    ``ValidationError`` 的包装根本接不到，三张面一致把它落成 E9000 / 500，而契约要的是
    E1010 / 422 / -32602。下面三格各按自己那面真实公开的读法断言。
    """
    smuggled = {"provider": "tdx"}

    recorder = _Recorder()
    with _test_client(recorder) as test_client:
        http = test_client.post("/v13/query/rates", json={"args": [], "kwargs": smuggled})
    assert http.status_code == 422, f"HTTP 把 kwargs 里的路由字段漏成了 {http.status_code}"
    http_body = http.json()["error"]
    assert http_body["context"]["reserved_fields"] == ["provider"]
    assert recorder.calls == [], "HTTP 面在拒绝之前就已经把请求递给了 Client"

    ws_error = _ws_reply("query", {"capability": "rates", "kwargs": smuggled})["error"]
    assert ws_error["code"] == ERR_INVALID_PARAMS
    assert ws_error["data"]["context"]["reserved_fields"] == ["provider"]

    server = MCPServer(_Recorder())  # type: ignore[arg-type]
    mcp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "query_capability",
                "arguments": {"capability": "rates", "kwargs": smuggled},
            },
        }
    )
    assert mcp is not None
    mcp_error = mcp["error"]
    assert mcp_error["code"] == ERR_INVALID_PARAMS
    assert mcp_error["data"]["context"]["reserved_fields"] == ["provider"]


def test_cli_query_refuses_routing_fields_smuggled_inside_kwargs() -> None:
    """CLI 面（``tstdx query --kwargs``）与三张 wire 面同口径、同一个拒绝原因。"""
    from argparse import Namespace

    from tstdx.cli.runtime_commands import cmd_query
    from tstdx.errors import ValidationError

    args = Namespace(
        capability="rates",
        args_json="[]",
        kwargs_json='{"channel": "quotation"}',
        provider=None,
        channel=None,
        currentness="business",
    )
    with pytest.raises(ValidationError) as caught:
        cmd_query(args)
    assert caught.value.code == "E1010"
    assert caught.value.context["face"] == "cli_query"
    assert caught.value.context["reserved_fields"] == ["channel"]
