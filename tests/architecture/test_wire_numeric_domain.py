"""整数请求域的三面一核核账（第 25 轮 G34）。

G34 有两半，这一份判据各钉一半：

(a) **谁把声明的边界当回事**。``count``/``start`` 这类整数请求字段过去四面各修各的：
    库面把坏值原样带到比较运算上、抛裸 :class:`TypeError`（四张服务面一致地把它翻译成
    E9000/HTTP 500）；WS 直接 ``int(params.get(...))``，一个非数字串炸出的 ``ValueError``
    被兜底 ``except Exception`` 登记成 **E9000 内部错误**；MCP 那个已删除的整数钳位把坏值
    **静默换成**另一个数（``count="abc"``→320、``count=0``→1、``count=99999999``→夹到上限），
    于是工具自己的 ``inputSchema`` 成了一张没人按它行事的假告示。现在三面共用
    :func:`tstdx.integration.wire_fields.as_request_int`，边界只有**声明**这一个来源。

(b) **谁付错数的代价**。最深的两道闸（``0xFFFF`` 单格上限与 ``start+count<=0x10000`` 分页
    地址空间）过去抛 :class:`~tstdx.errors.ParseError`，而它对外发布 **HTTP 502 +
    RetryAdvice(retryable=True, switch_host=True)**——调用方写错一个参数，得到的回答是
    "上游坏了，换个主机重试"。这类守卫现在统一收在 E1010/422/不可重试那一侧。

分母一律现取，不抄第二份：MCP 读 9 张 ``inputSchema``，HTTP 读 FastAPI 路由自己的
``dependant.query_params``（``Ge``/``Le`` 元数据），WS 读 :data:`WS_PARAMS_FIELDS`，内核读
:class:`~tstdx.query.QuerySpec` 的 ``int`` 注解与 ``0xFFFF`` 那道闸。
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest
from annotated_types import Ge, Le

from tstdx.client import TdxClient
from tstdx.error_envelope import to_error_envelope
from tstdx.errors import ValidationError
from tstdx.integration import wire_fields
from tstdx.integration.mcp._server import MCPServer
from tstdx.integration.mcp._tools_impl import _int_arg
from tstdx.integration.mcp._tools_spec import _TOOLS_BY_NAME
from tstdx.integration.runtime_ws import ERR_INTERNAL, ERR_INVALID_PARAMS, RuntimeJsonRpcHandler
from tstdx.query import SPEC_INT_FIELDS, QuerySpec

ROOT = Path(__file__).resolve().parents[2]

#: 三张服务面上把请求整数换算成内核参数的代码。第 24 轮之前的 ``int(params.get(...))``
#: 与 MCP 的钳位都长在这些文件里。
FACE_MODULES = (
    "tstdx/integration/runtime_ws.py",
    "tstdx/integration/runtime_http.py",
    "tstdx/integration/mcp/_tools_impl.py",
)

#: 每个"看起来像请求取值"的形状都算一次越权换算的嫌疑对象。
_REQUEST_CONTAINERS = frozenset({"params", "args", "kwargs", "query_params"})

#: 客户端错误在 wire 上的三格身份（G34 (b) 的那半）。
_CLIENT_IDENTITY = ("E1010", 422, False)


def _client_stub() -> Any:
    """三张服务面挂的都是内核门面（``Client``），不是同步 ``TdxClient``。

    用错门面会让 ``bars(provider=...)`` 先撞一个 ``TypeError``（``TdxClient`` 的 typed 签名里
    没有这一格），于是本判据量到的是自己的接线错误而不是请求域。内核在构造期不做 I/O，
    而这里递进去的每个值都必须在 I/O 之前被拒——真上了线，信封会是 E2xxx 而不是 E1010，
    下面那些断言会当场红，不会假装绿。
    """
    from tstdx.client.api import Client

    return Client()


def _assert_client_side(exc: ValidationError) -> None:
    envelope = to_error_envelope(exc)
    assert (envelope.code, envelope.http_status, envelope.retryable) == _CLIENT_IDENTITY, (
        f"调用方写错的入参被发布成 {envelope.code}/"
        f"{envelope.http_status}/retryable={envelope.retryable}"
    )


def _raises_client_side(match: str | None = None) -> Any:
    return pytest.raises(ValidationError, match=match)


def _raw_request_conversions(source: str) -> list[int]:
    """列出"对请求容器的取值直接 ``int()``/``float()``"的调用点行号。"""

    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in {"int", "float"} or not node.args:
            continue
        for inner in ast.walk(node.args[0]):
            #: 两条判据各写成一条 `if`：`ruff` 的 SIM102 会顺手把嵌套压平，而压平后的写法要求
            #: mypy 相信"外层 isinstance 的收窄在 `and` 链里继续成立"——这条判据正是来抓那种
            #: 取值的，不能自己先冒一次未检查的属性访问。
            func = getattr(inner, "func", None)
            if (
                isinstance(inner, ast.Call)
                and isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Name)
                and func.value.id in _REQUEST_CONTAINERS
            ):
                hits.append(node.lineno)
                break
            container = getattr(inner, "value", None)
            if (
                isinstance(inner, ast.Subscript)
                and isinstance(container, ast.Name)
                and container.id in _REQUEST_CONTAINERS
            ):
                hits.append(node.lineno)
                break
    return hits


def _mcp_integer_props() -> dict[str, dict[str, dict[str, Any]]]:
    """``{tool: {property: schema}}``，只收 ``type == "integer"`` 的那几格。"""

    return {
        tool.name: {
            prop: spec
            for prop, spec in tool.inputSchema.get("properties", {}).items()
            if spec.get("type") == "integer"
        }
        for tool in _TOOLS_BY_NAME.values()
    }


def _http_integer_query_params() -> list[tuple[str, str, int | None, int | None]]:
    """``(path, name, ge, le)``——分母是 FastAPI 路由自己的签名，不是文档抄件。"""

    from tstdx.integration.runtime_http import create_runtime_app

    app = create_runtime_app(_client_stub())
    out: list[tuple[str, str, int | None, int | None]] = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for param in dependant.query_params:
            info = param.field_info
            if info.annotation is not int:
                continue
            ge = next((m.ge for m in info.metadata if isinstance(m, Ge)), None)
            le = next((m.le for m in info.metadata if isinstance(m, Le)), None)
            out.append((getattr(route, "path", "?"), param.name, ge, le))
    return out


def test_the_two_wire_faces_no_longer_convert_request_integers_themselves() -> None:
    """AST：请求容器的取值不许再被就地 ``int()``，也不许有第二个钳位实现。"""

    findings = {
        relative: _raw_request_conversions((ROOT / relative).read_text(encoding="utf-8"))
        for relative in FACE_MODULES
    }
    assert not any(findings.values()), f"越权换算又长回来了：{findings}"
    assert not any(hasattr(wire_fields, name) for name in ("clamp_int", "coerce_int")), (
        "静默替换坏值的钳位回到了唯一拒绝口上"
    )
    #: 正控：两种历史形状各造一次，尺子必须各点一行。
    planted = (
        "def _dispatch(params):\n"
        "    count = int(params.get('count'))\n"
        "    start = int(args['start'])\n"
        "    return count, start\n"
    )
    assert len(_raw_request_conversions(planted)) == 2, "尺子对两种历史形状失明了"
    #: 反向：合法的非请求换算不许被误报（``int(exc.status_code)`` 就在 runtime_http 里）。
    assert _raw_request_conversions("def h(exc):\n    return int(exc.status_code)\n") == []


def test_mcp_enforces_exactly_the_bounds_its_own_schema_declares() -> None:
    """``inputSchema`` 的 ``minimum``/``maximum``/``default`` 是执行值，不是告示。"""

    scanned = _mcp_integer_props()
    fields = [(tool, prop) for tool, props in scanned.items() for prop in props]
    assert len(fields) >= 5, f"MCP 整数格只扫出 {len(fields)} 处，schema 的形状变了"
    for tool, prop in fields:
        spec = scanned[tool][prop]
        low, high, default = spec.get("minimum"), spec.get("maximum"), spec.get("default", 0)
        assert _int_arg(tool, prop, None) == default, f"{tool}.{prop} 的缺省与声明不符"
        for bound in (low, high):
            if bound is not None:
                assert _int_arg(tool, prop, bound) == bound, (
                    f"{tool}.{prop} 误拒了自己声明的 {bound}"
                )
                with _raises_client_side() as raised:
                    _int_arg(tool, prop, bound + (-1 if bound is low else 1))
                _assert_client_side(raised.value)
        for shape in ("abc", 1.5, True, [1], {"a": 1}, ""):
            with pytest.raises(ValidationError):
                _int_arg(tool, prop, shape)


def test_the_mcp_bound_comes_from_the_schema_not_from_a_hardcoded_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正控：把声明改小，执行必须跟着改小——否则边界是抄件，声明可以随便改。"""

    spec = _TOOLS_BY_NAME["get_bars"].inputSchema["properties"]["count"]
    monkeypatch.setitem(spec, "maximum", 7)
    assert _int_arg("get_bars", "count", 7) == 7
    with _raises_client_side("区间") as raised:
        _int_arg("get_bars", "count", 8)
    _assert_client_side(raised.value)
    #: 未声明边界的格（``get_bars.count`` 的 minimum 是 1）改大也要跟着变大。
    monkeypatch.setitem(spec, "maximum", 9000)
    assert _int_arg("get_bars", "count", 9000) == 9000


def test_ws_answers_a_bad_integer_as_invalid_params_not_internal_error() -> None:
    """WS 现场：坏整数必须是 -32602（客户端那一侧），不能掉进 ``except Exception``。"""

    handler = RuntimeJsonRpcHandler(_client_stub())
    for params in (
        {"symbol": "600519", "count": "abc"},
        {"symbol": "600519", "count": 1.5},
        {"symbol": "600519", "count": [3]},
        {"symbol": "600519", "start": "x"},
    ):
        response = _ws_call(handler, "bars", params)
        assert response["error"]["code"] == ERR_INVALID_PARAMS, response
        assert response["error"]["data"]["code"] == "E1010", response
        assert response["error"]["data"]["retryable"] is False, response
    #: 越界但形状正确的值由 WS 放行、由内核那道 0xFFFF 闸接住——两半都得是客户端错误。
    response = _ws_call(handler, "bars", {"symbol": "600519", "count": 70000})
    assert response["error"]["code"] != ERR_INTERNAL, f"内核闸把调用方的数报成内部错误：{response}"
    assert response["error"]["data"]["code"] == "E1010", response
    #: 正控：同一把尺在"确实没人声明过"的键上必须换一种报法（未知键也是 -32602，但口径不同）。
    unknown = _ws_call(handler, "bars", {"symbol": "600519", "nope": 1})
    assert unknown["error"]["code"] == ERR_INVALID_PARAMS, unknown
    assert "nope" in unknown["error"]["data"]["message"], unknown


def _ws_call(handler: RuntimeJsonRpcHandler, method: str, params: dict[str, Any]) -> dict[str, Any]:
    import json

    raw = handler.handle_message(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    assert raw is not None
    return json.loads(raw)


def test_mcp_tool_call_over_the_wire_reports_the_same_side_of_the_line() -> None:
    """端到端：``tools/call`` 递一个越界 count，JSON-RPC 层给 -32602 而不是 -32603。"""

    import json

    server = MCPServer(_client_stub())
    maximum = _TOOLS_BY_NAME["get_bars"].inputSchema["properties"]["count"]["maximum"]
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "get_bars",
                "arguments": {"symbol": "600519", "count": maximum + 1},
            },
        }
    )
    assert response is not None
    assert response["error"]["code"] == ERR_INVALID_PARAMS, response
    assert response["error"]["data"]["code"] == "E1010", response
    #: 越界的整数绝不能被替换成别的数以后再发出去。
    assert "count" in json.dumps(response, ensure_ascii=False, default=str)


def test_http_declared_bounds_answer_422_before_any_handler_runs() -> None:
    """HTTP 的 ``Query(ge/le)`` 与另两面的拒绝口给出同一侧的下场。"""

    from fastapi.testclient import TestClient

    from tstdx.integration.runtime_http import create_runtime_app

    params = _http_integer_query_params()
    bounded = [row for row in params if row[2] is not None or row[3] is not None]
    assert bounded, "HTTP 侧一个带边界的整数查询参数都没扫到，本判据没有对象"
    client = TestClient(create_runtime_app(_client_stub()), base_url="http://testserver")
    for path, name, ge, le in bounded:
        url = path.replace("{symbol}", "600519")
        for value in ([] if le is None else [le + 1]) + ([] if ge is None else [ge - 1]):
            response = client.get(url, params={name: value})
            assert response.status_code == 422, f"{url}?{name}={value} -> {response.status_code}"
            body = response.json()
            assert body.get("error", body).get("code", "E1010") != "E9000", body
    #: 形状正确的边界值不会被 HTTP 拒（这一格只声明下界的参数在这里必须放行到处理器），
    #: 所以这一条只对同时声明了上下界的参数成立；扫不到就不断言，不做无对象的绿。
    closed = [row for row in bounded if row[2] is not None and row[3] is not None]
    assert closed, "HTTP 侧没有同时声明上下界的整数参数，上面那格的 le+1/ge-1 有一侧是空的"


def test_the_binder_guards_every_integer_grid_declared_on_the_spec() -> None:
    """绑定处的后闸：``QuerySpec`` 声明为 ``int`` 的每一格都不许漏给裸 ``TypeError``。"""

    annotated = {f.name for f in dataclasses.fields(QuerySpec) if f.type == "int"}
    defaulted = {
        f.name
        for f in dataclasses.fields(QuerySpec)
        if isinstance(f.default, int) and not isinstance(f.default, bool)
    }
    assert set(SPEC_INT_FIELDS) == annotated == defaulted, (
        f"整数格三扫不一致：声明 {annotated} / 默认值 {defaulted} / 守卫 {set(SPEC_INT_FIELDS)}"
    )
    spec = QuerySpec.build("bars", symbols="600519", count=5)
    for name in SPEC_INT_FIELDS:
        with _raises_client_side("必须是整数") as raised:
            dataclasses.replace(spec, **{name: "320"}).normalized()
        _assert_client_side(raised.value)


def test_the_page_ceiling_is_a_client_error_on_every_face() -> None:
    """0xFFFF 与 ``start+count`` 那道闸：改前是 502 + 建议换主机，改后是 422。"""

    for client in (TdxClient(pool=object()), _client_stub()):
        with _raises_client_side("16-bit") as raised:
            client.bars("600519", start=65530, count=10)
        _assert_client_side(raised.value)
        with _raises_client_side("超过上限") as raised:
            client.bars("600519", count=70000)
        _assert_client_side(raised.value)
    #: 正控：仍在域内的调用不许被这条尺子量成错误（否则上面两格只是恒真的类名检查）。
    assert _int_arg("get_bars", "count", 320) == 320
    assert to_error_envelope(ValidationError("x")).retryable is False
    assert to_error_envelope(ValidationError("x")).http_status == 422
