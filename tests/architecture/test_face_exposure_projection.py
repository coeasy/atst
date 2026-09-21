# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""四张服务面必须是同一张声明表的投影——**并且要一致地正确**（V18-C1 第一段）。

第 10 轮先量后改。量的不是"某一面有没有说谎"，而是"同一个业务事实在几个地方各说一遍"。
 ``(provider, channel, capability) → 执行体`` 这张表在 ``runtime/executor.py`` 里是唯一的，
``DEDICATED_CAPABILITIES`` 就从它派生；而"哪些能力走专用路径、每个旋钮在哪个面上叫什么"
这件事，内核、HTTP、WS、MCP、CLI 各自另抄了一份。抄件本身没错——实测七条能力、九个旋钮
在四面当前**逐格相等**。真正的问题在第七节那条判据照不到的地方：

**四面一致 ≠ 四面正确。** ``FallbackPolicy.build`` 与 ``Client.quotes/bars`` 的入参冲突原本抛
裸 ``ValueError``，于是四面**一致地**把调用方写错的键报成 ``E9000 / HTTP 500 / JSON-RPC
-32603 / CLI 退出码 1``，人读的那句话被统一改写成 "internal error"、``context`` 清空。
第 5 步为每张面各自装的判据一个都没响：它们用的都是替身 ``Client``，看见的是"字段有没有
被转出去"，而归类发生在内核算线的那一刻，替身从来不算线。CLI 更是把自己写在
``cli/__init__.py`` 里的承诺反过来说了——那里写着「领域错误走退出码 2，与 E9000/退出码 1
的原生未捕获异常区分，便于脚本判定失败类别」。

所以这里的判据全部按**同一个值打进四面、看内核到底收到什么**来构造，不抄任何名单：

一 **面 ↔ 派生集**：每一面的专用入口，落点必须正好是 ``DEDICATED_CAPABILITIES``，少一格＝
    这条能力在那个面上提不出来，多一格＝对外承诺了一条没有专属执行体的查询。
二 **声明 ⇒ 推动**：一张面上声明的每个数据字段，换掉它必须能改变内核收到的那一次调用；
    改不动的就是 wire 面版的 ``max_age``（第 23 步删掉的那类幻影旋钮）。
三 **跨面同进同退**：某个内核形参要么四面都能推动，要么一面都不能——只在一面能推动的旋钮
    迟早会变成"这份文档说没有"的那一格（本轮实测唯一违例：``fallback`` 只有 MCP 缺）。
四 **尺子自检**：派生集与四面的入口规模低于下限时先红，免得"零违例"是读空的。
五 **入参错误归类**：``fallback`` 这一族三种写错的方式在四面都必须落在 E1010 那一类；
    一次执行面泄漏（探针的防火墙 ``E0000``）与一次 ``E9000`` 都不许出现。
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
from collections.abc import Callable
from typing import Any

import pytest

import tstdx.cli.runtime_commands as cli_runtime
from tstdx.cli.parser import build_parser
from tstdx.client.api import Client
from tstdx.errors import ValidationError
from tstdx.integration.mcp._server import MCPServer
from tstdx.integration.mcp._tools_spec import TOOLS
from tstdx.integration.runtime_http import create_runtime_app
from tstdx.integration.runtime_ws import RuntimeJsonRpcHandler
from tstdx.integration.wire_fields import WS_PARAMS_FIELDS
from tstdx.runtime.executor import DEDICATED_CAPABILITIES
from tstdx.runtime.orchestration import FallbackPolicy

#: 探针替身的执行面泄漏码：任何"值合法到足以开始查询"的形状都会以它显形，而不是变成一次
#: 真实请求。本文件里凡出现 E0000，含义都是"这一格根本没有被入参判据挡住"。
FIREWALL_CODE = "E0000"

#: 字段样本，只用于"换一个值进去"：它不是任何契约的名单，声明面自己才是。
SAMPLES: dict[str, tuple[Any, Any]] = {
    "symbol": ("600519", "000001"),
    "symbols": (["600519"], ["000001"]),
    "provider": ("tdx", "sina"),
    "fallback": ("tdx", "sina"),
    "period": ("day", "1min"),
    "count": (2, 5),
    "start": (1, 3),
    "adjustment": ("qfq", "hfq"),
    "market": ("1", "0"),
    # 连接旋钮：它不该改内核收到的那一次调用，它改的是内核**怎么被构造出来**——
    # 所以判据二比的是「调用 + 构造」两个槽位，两头都不动的才是幻影旋钮。
    "host": ("127.0.0.1:1", "127.0.0.1:2"),
}

FACES = ("http", "ws", "mcp", "cli")


class Recorder:
    """内核替身：只记账——哪个方法、什么位置参数、什么关键字参数。"""

    def __init__(self) -> None:
        from tstdx.query import QueryPlanner, QuerySpec
        from tstdx.result import Provenance, QueryResult

        plan = QueryPlanner().compile(QuerySpec.build("rates", provider="boc"))
        self.result = QueryResult.from_plan(
            [{"currency": "USD"}], plan=plan, provenance=Provenance.direct(plan)
        )
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return ("rates", "balance_sheet")

    @property
    def runtime(self) -> Any:
        return type("R", (), {"planner": type("P", (), {"default_provider": "tdx"})()})()

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return self.result

        return _record


def _samples(keys: Any) -> dict[str, Any]:
    missing = sorted(set(keys) - set(SAMPLES))
    assert not missing, f"这些声明字段没有样本值，判据会被架空：{missing}"
    return {key: SAMPLES[key][0] for key in keys}


def _one(keys: Any, field: str) -> dict[str, Any]:
    filled = _samples(keys)
    filled[field] = SAMPLES[field][1]
    return filled


# ------------------------------------------------------------------ 四面驱动
_HTTP_PATHS = {
    "quotes": "/v13/quotes",
    "bars": "/v13/bars/{symbol}",
    "snapshot": "/v13/snapshot/{symbol}",
    "minute": "/v13/minute/{symbol}",
    "trades": "/v13/trades/{symbol}",
    "security_count": "/v13/security/count",
    "security_list": "/v13/security/list",
}
_WS_METHODS = {
    "quotes": "quotes",
    "bars": "bars",
    "snapshot": "snapshot",
    "minute": "minute",
    "trades": "trades",
    "security_count": "security.count",
    "security_list": "security.list",
}


def _http_fields(path: str) -> list[str]:
    app = create_runtime_app(Recorder())  # type: ignore[arg-type]
    route = next(r for r in app.routes if getattr(r, "path", "") == path)
    return [p.name for p in route.dependant.query_params] + [
        p.name for p in route.dependant.path_params
    ]


def _drive_http(entry: str, fields: dict[str, Any]) -> tuple[str, tuple, dict, dict]:
    from fastapi.testclient import TestClient

    rec = Recorder()
    url, query = entry, {}
    for key, value in fields.items():
        if "{" + key + "}" in entry:
            url = url.replace("{" + key + "}", str(value))
        else:
            query[key] = value
    with TestClient(create_runtime_app(rec), raise_server_exceptions=False) as tc:  # type: ignore[arg-type]
        response = tc.get(url, params=query)
    if response.status_code != 200:
        raise AssertionError(f"GET {entry} 带已声明字段却没通：{response.text[:200]}")
    return (*rec.calls[-1], {})


def _drive_ws(entry: str, fields: dict[str, Any]) -> tuple[str, tuple, dict, dict]:
    rec = Recorder()
    raw = RuntimeJsonRpcHandler(rec).handle_message(  # type: ignore[arg-type]
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": entry, "params": fields})
    )
    reply = json.loads(str(raw))
    assert "error" not in reply, f"WS {entry} 带已声明字段却被拒：{reply}"
    return (*rec.calls[-1], {})


def _drive_mcp(entry: str, fields: dict[str, Any]) -> tuple[str, tuple, dict, dict]:
    rec = Recorder()
    server = MCPServer(rec)  # type: ignore[arg-type]
    reply = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": entry, "arguments": fields},
        }
    )
    assert reply is not None and "error" not in reply, f"MCP {entry} 带已声明字段却被拒：{reply}"
    return (*rec.calls[-1], {})


_parser = build_parser()


def _cli_actions(command: str) -> list[Any]:
    sub = _parser._subparsers._group_actions[0].choices[command]  # type: ignore[union-attr]
    return [action for action in sub._actions if action.dest != "help"]


def _cli_fields(command: str) -> list[str]:
    return [action.dest for action in _cli_actions(command)]


def _drive_cli(entry: str, fields: dict[str, Any]) -> tuple[str, tuple, dict, dict]:
    rec = Recorder()
    positional: list[str] = []
    options: list[str] = []
    for action in _cli_actions(entry):
        if action.dest not in fields:
            continue
        value = fields[action.dest]
        if not action.option_strings:
            positional += [str(item) for item in value] if isinstance(value, list) else [str(value)]
        elif isinstance(value, list):
            options += [action.option_strings[-1], *[str(item) for item in value]]
        else:
            options += [action.option_strings[-1], str(value)]
    args = _parser.parse_args([entry, *positional, *options])
    real = cli_runtime.Client
    ctx = _Ctx(rec)
    cli_runtime.Client = lambda *a, **k: ctx(*a, **k)  # type: ignore[assignment]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            getattr(cli_runtime, f"cmd_{entry.replace('-', '_')}")(args)
    finally:
        cli_runtime.Client = real  # type: ignore[misc]
    assert rec.calls, f"CLI {entry} 没走到内核"
    return (*rec.calls[-1], ctx.ctor)


class _Ctx:
    """让 ``with Client(...)`` 拿到替身，并记下它被怎么构造出来。"""

    def __init__(self, rec: Recorder) -> None:
        self._rec = rec
        self.ctor: dict[str, Any] = {}

    def __call__(self, *args: Any, **kwargs: Any) -> _Ctx:
        self.ctor = dict(kwargs)
        return self

    def __enter__(self) -> Recorder:
        return self._rec

    def __exit__(self, *exc: Any) -> bool:
        return False


#: 每一面的「专用入口」清单：入口标识 → (驱动函数, 该入口声明的字段)。全部现算，不抄。
def _face_entries() -> dict[str, dict[str, tuple[Callable[[str, dict], Any], list[str]]]]:
    entries: dict[str, dict[str, tuple[Callable[[str, dict], Any], list[str]]]] = {
        "http": {path: (_drive_http, _http_fields(path)) for path in _HTTP_PATHS.values()},
        "ws": {
            method: (_drive_ws, sorted(WS_PARAMS_FIELDS[method])) for method in _WS_METHODS.values()
        },
        "mcp": {
            tool.name: (_drive_mcp, sorted(tool.inputSchema["properties"]))
            for tool in TOOLS
            if tool.name != "query_capability"
        },
        "cli": {
            cap.replace("_", "-"): (_drive_cli, _cli_fields(cap.replace("_", "-")))
            for cap in sorted(DEDICATED_CAPABILITIES)
        },
    }
    return entries


def _kernel_method(face: str, entry: str, fields: dict[str, Any]) -> tuple[str, tuple, dict]:
    drive, _ = _face_entries()[face][entry]
    return drive(entry, fields)


# ------------------------------------------------------------------ 判据
def test_dedicated_set_is_what_the_executor_table_says() -> None:
    """``DEDICATED_CAPABILITIES`` 就是"有专属执行体的绑定"，不是第四份同名清单。"""
    from tstdx.runtime.executor import DIRECT_BINDINGS

    derived = {
        binding.capability
        for binding in DIRECT_BINDINGS
        if binding.executor_name != "_migrated_capability"
    }
    assert derived == DEDICATED_CAPABILITIES, "派生集与绑定表自身都对不上了"
    assert len(DEDICATED_CAPABILITIES) >= 7, (
        f"派生集只剩 {sorted(DEDICATED_CAPABILITIES)}，下面的四方比对没有对象"
    )
    # 内核便捷方法与这张表也必须是同一件事，而不是"各自写了恰好相同的七行"。
    for capability in sorted(DEDICATED_CAPABILITIES):
        assert callable(getattr(Client, capability, None)), (
            f"{capability} 有专属执行体却没有 Client 便捷方法：它到底是谁的面？"
        )


@pytest.mark.parametrize("face", FACES)
def test_every_face_is_a_total_projection_of_the_table(face: str) -> None:
    """一：每一面的专用入口都落在派生集上，且把派生集**全覆盖**。"""
    landing: dict[str, str] = {}
    for entry, (_drive, fields) in sorted(_face_entries()[face].items()):
        method = _kernel_method(face, entry, _samples(fields))[0]
        assert method in DEDICATED_CAPABILITIES, (
            f"{face} 的入口 {entry} 落在 {method!r}：它不是这张表里的专属执行体，"
            "要么是网关/元信息入口混进了专用清单，要么是凭空多出来的一张面"
        )
        landing[entry] = method
    uncovered = sorted(DEDICATED_CAPABILITIES - set(landing.values()))
    assert not uncovered, f"{face} 面没有任何入口能走到这些专属能力：{uncovered}"


@pytest.mark.parametrize("face", FACES)
def test_every_declared_field_moves_the_kernel_call(face: str) -> None:
    """二：声明过的数据字段，换掉它必须改变内核收到的那一次调用。"""
    problems: list[str] = []
    for entry, (drive, fields) in sorted(_face_entries()[face].items()):
        base = drive(entry, _samples(fields))
        for field in fields:
            if field not in SAMPLES:  # pragma: no cover - _samples 已先红
                continue
            changed = drive(entry, _one(fields, field))
            if _delta(base, changed) == set():
                problems.append(f"{face}:{entry} 的 {field}")
    assert not problems, f"这些声明过的字段换值之后内核毫无反应（wire 面版的 max_age）：{problems}"


def _delta(base: tuple, changed: tuple) -> set[str]:
    """两次内核往返差在哪些槽位上。

    位置参数记到 ``<positional>``（由调用方按能力签名翻译成第一个形参）；第四格是
    ``Client`` 的构造参数，连接类旋钮只动得了那里。
    """
    out: set[str] = set()
    if base[0] != changed[0]:
        out.add("<method>")
    if base[1] != changed[1]:
        out.add("<positional>")
    for key in set(base[2]) | set(changed[2]):
        if base[2].get(key, "<absent>") != changed[2].get(key, "<absent>"):
            out.add(key)
    for key in set(base[3]) | set(changed[3]):
        if base[3].get(key, "<absent>") != changed[3].get(key, "<absent>"):
            out.add(f"<ctor:{key}>")
    return out


def test_a_knob_is_on_every_face_or_on_none() -> None:
    """三：同一个内核形参在四面上的"能不能推动"必须一致。"""
    movable: dict[str, dict[str, set[str]]] = {}
    for face in FACES:
        per_capability: dict[str, set[str]] = {}
        for entry, (drive, fields) in _face_entries()[face].items():
            base = drive(entry, _samples(fields))
            capability = base[0]
            params = _slot_names(capability)
            moved: set[str] = set()
            for field in fields:
                if field not in SAMPLES:  # pragma: no cover
                    continue
                delta = _delta(base, drive(entry, _one(fields, field)))
                if "<positional>" in delta:
                    delta.discard("<positional>")
                    delta.add(params[0])
                moved |= delta & set(params)
            per_capability[capability] = per_capability.get(capability, set()) | moved
        movable[face] = per_capability

    problems: list[str] = []
    for capability in sorted(DEDICATED_CAPABILITIES):
        for param in _slot_names(capability):
            have = [face for face in FACES if param in movable[face].get(capability, set())]
            if have and len(have) != len(FACES):
                problems.append(
                    f"{capability}.{param} 只有 {have} 能推动，缺 {sorted(set(FACES) - set(have))}"
                )
    assert not problems, "这些旋钮只挂在部分面上了：\n" + "\n".join(problems)


def _slot_names(capability: str) -> list[str]:
    params = [p for p in inspect.signature(getattr(Client, capability)).parameters if p != "self"]
    assert params, f"{capability} 的签名读空了，判据自身失效"
    return params


@pytest.mark.parametrize(
    ("tool", "subject"),
    [
        ("get_bars", {"symbol": "600519"}),
        ("get_quote", {"symbol": "600519"}),
        ("get_quotes", {"symbols": ["600519"]}),
    ],
)
def test_the_mcp_provider_default_yields_to_fallback(tool: str, subject: dict[str, Any]) -> None:
    """``fallback`` 缺席时面的 ``provider`` 缺省照常生效，在场时它必须让位。

    内核把「点名一家」与「这家失败就换」判成互斥（``_reject_provider_with_policy``），所以
    MCP 若继续把 ``provider`` 钉成默认那一家，**每一个**带 ``fallback`` 的 MCP 调用都会撞在
    这条互斥上；反过来把缺省整个删掉，不带 ``fallback`` 的调用就又换了一家。两种下场都靠
    这一格按住——它查的是"两个旋钮同时出现时谁说话"，不是某个具体缺省值。
    """
    without = _drive_mcp(tool, dict(subject))[2]
    with_fallback = _drive_mcp(tool, {**subject, "fallback": "sina"})[2]
    assert without["policy"] is None, f"{tool} 没要 fallback 却自己造了一份名单"
    assert with_fallback["policy"] is not None, f"{tool} 把 fallback 弄丢了"
    assert with_fallback["provider"] is None, (
        f"{tool} 的 provider 缺省没给 fallback 让位：调用方说「换着来」，面却替他钉死了 {with_fallback['provider']!r}"
    )


@pytest.fixture(scope="module")
def kernel() -> Any:
    """一个真 ``Client``（构造不触网），用来问内核分派本身；请求一律在便捷方法层截住。"""
    with Client() as client:
        yield client


def _spy(rec: Recorder, name: str) -> Callable[..., Any]:
    def _impl(*args: Any, **kwargs: Any) -> Any:
        rec.calls.append((name, args, kwargs))
        return rec.result

    return _impl


def test_the_core_dispatch_cannot_fall_through_to_a_neighbour(
    kernel: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """五的前半：``Client.call`` 分派到哪个便捷方法，逐个专属能力现问。

    这一段原来是 ``if/elif`` 加一条 ``else``，而 ``else`` 里写的是 ``security_list``：
    "核心集里多出一个名字"的下场不是报错，而是拿隔壁能力去跑那个请求。
    """
    rec = Recorder()
    problems: list[str] = []
    for capability in sorted(DEDICATED_CAPABILITIES):
        monkeypatch.setattr(kernel, capability, _spy(rec, capability), raising=False)
    for capability in sorted(DEDICATED_CAPABILITIES):
        # 递不递位置参数由内核签名自己决定：``security_*`` 是纯关键字签名，硬塞一个
        # 位置参数会撞上它自己的入参判据，那就不是在测分派了。
        first = list(inspect.signature(getattr(Client, capability)).parameters.values())[1]
        subject = () if first.kind is inspect.Parameter.KEYWORD_ONLY else ("600519",)
        kernel.call(capability, *subject)
        landed = rec.calls[-1][0]
        if landed != capability:
            problems.append(f"call({capability!r}) 实际走到 {landed!r}")
    assert not problems, "内核分派把能力送错了便捷方法：\n" + "\n".join(problems)


def test_unknown_core_capability_is_refused_not_borrowed(
    kernel: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """五的后半：派生集里凭空多出一个名字时，必须当场拒，而不是借用邻格。"""
    rec = Recorder()
    inflated = frozenset(set(DEDICATED_CAPABILITIES) | {"snapshot_v2"})
    monkeypatch.setattr("tstdx.client.api._CORE_CAPABILITIES", inflated)
    monkeypatch.setattr(kernel, "security_list", _spy(rec, "security_list"), raising=False)
    with pytest.raises(ValidationError) as caught:
        kernel.call("snapshot_v2", "600519")
    assert "snapshot_v2" in str(caught.value)
    assert not rec.calls, (
        f"没有分支的能力被借用去跑了 {rec.calls[-1][0]!r}——声明表派生出来的东西，分派必须也派生自它"
    )


@pytest.mark.parametrize("face", FACES)
def test_a_bad_fallback_is_an_input_error_on_every_face(
    face: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """五：``fallback`` 写错的三种方式，四面都必须说成入参错误（E1010），不许是 E9000。

    用真 ``Client``：这一族判据要测的正是"内核算线之后错误被报成什么"，替身永远不算线。
    执行面被换成抛 :data:`FIREWALL_CODE` 的桩，所以任何漏过入参校验的值都会以它显形，
    而不是变成一次真实请求。
    """
    _firewall(monkeypatch)
    problems: list[str] = []
    for label, request in _bad_fallback_cases()[face]:
        envelope = request()
        if envelope["code"] == FIREWALL_CODE:
            problems.append(f"「{label}」根本没被入参判据挡住，已经走到执行面")
        elif envelope["code"] != "E1010":
            problems.append(
                f"「{label}」没有落在入参那一类，而是 {envelope['code']}"
                f"（{str(envelope['message'])[:60]}）：调用方写错的键被抹成了服务器故障"
            )
    assert not problems, f"{face} 面的 fallback 入参错误归类不对：\n" + "\n".join(problems)


def _firewall(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise ValidationError("探针防火墙：这一格已经走到执行面", code=FIREWALL_CODE)

    from tstdx.runtime.kernel import UnifiedRuntime
    from tstdx.runtime.orchestration import ProviderOrchestrator

    monkeypatch.setattr(UnifiedRuntime, "execute", _blocked)
    monkeypatch.setattr(ProviderOrchestrator, "execute", _blocked)


def _envelope_of(exc: BaseException) -> dict[str, Any]:
    from tstdx.error_envelope import to_error_envelope

    return to_error_envelope(exc).to_dict()


def _bad_fallback_cases() -> dict[str, list[tuple[str, Callable[[], dict[str, Any]]]]]:
    """三种"把 fallback 写错"的形状 × 四面，每种都返回它的错误信封。"""
    from fastapi.testclient import TestClient

    combos: list[tuple[str, dict[str, Any]]] = [
        ("和 provider 同时给出", {"symbols": "600519", "provider": "tdx", "fallback": "sina"}),
        ("名单里有重复", {"symbols": "600519", "fallback": "tdx,tdx"}),
        ("只有分隔符", {"symbols": "600519", "fallback": ","}),
    ]

    def http_case(query: dict[str, Any]) -> Callable[[], dict[str, Any]]:
        def _run() -> dict[str, Any]:
            with TestClient(create_runtime_app(), raise_server_exceptions=False) as tc:
                response = tc.get("/v13/quotes", params=query)
            assert response.status_code != 200, "这一格居然查通了，判据要看的是它的失败"
            return response.json()["error"]

        return _run

    def ws_case(params: dict[str, Any]) -> Callable[[], dict[str, Any]]:
        def _run() -> dict[str, Any]:
            raw = RuntimeJsonRpcHandler().handle_message(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "quotes", "params": params})
            )
            reply = json.loads(str(raw))
            error = reply.get("error") or {}
            data = error.get("data") or {}
            assert data, f"WS 没有把这一格报成错误：{reply}"
            return data

        return _run

    def mcp_case(arguments: dict[str, Any]) -> Callable[[], dict[str, Any]]:
        def _run() -> dict[str, Any]:
            reply = MCPServer().handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "get_quotes", "arguments": arguments},
                }
            )
            assert reply is not None
            data = (reply.get("error") or {}).get("data") or {}
            assert data, f"MCP 没有把这一格报成错误：{reply}"
            return data

        return _run

    def cli_case(argv: list[str]) -> Callable[[], dict[str, Any]]:
        def _run() -> dict[str, Any]:
            from tstdx.cli import main

            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = main(argv)
            assert rc != 0, f"CLI 这一格返回了成功退出码：{rc}"
            return json.loads(err.getvalue().strip())["error"]

        return _run

    return {
        "http": [(label, http_case(query)) for label, query in combos],
        "ws": [
            (label, ws_case({**query, "symbols": list(query["symbols"].split(","))}))
            for label, query in combos
        ],
        "mcp": [
            (label, mcp_case({**query, "symbols": list(query["symbols"].split(","))}))
            for label, query in combos
        ],
        "cli": [
            (
                label,
                cli_case(
                    [
                        "quotes",
                        "600519",
                        *(["--provider", query["provider"]] if "provider" in query else []),
                        "--fallback",
                        query["fallback"],
                    ]
                ),
            )
            for label, query in combos
        ],
    }


def test_fallback_wire_parser_is_the_only_one() -> None:
    """四的自检：四面共用的解析只有 ``FallbackPolicy.from_wire`` 一处。"""
    assert FallbackPolicy.from_wire(None) is None
    assert FallbackPolicy.from_wire("") is None
    assert FallbackPolicy.from_wire("tdx,sina") == FallbackPolicy.build("tdx", "sina")
    assert FallbackPolicy.from_wire(["tdx", "sina"]) == FallbackPolicy.build("tdx", "sina")
    for bad in ("tdx,tdx", ",", "not_a_provider", 7):
        with pytest.raises(ValidationError):
            FallbackPolicy.from_wire(bad)

    import ast
    import textwrap

    for target in (
        cli_runtime._policy,
        RuntimeJsonRpcHandler._policy,
    ):
        body = ast.parse(textwrap.dedent(inspect.getsource(target)))
        split_calls = [
            node
            for node in ast.walk(body)
            if isinstance(node, ast.Attribute) and node.attr == "split"
        ]
        assert not split_calls, (
            f"{target.__qualname__} 又自己切了一遍逗号：同一条解析在两个地方各说一遍，"
            "就会有一份过期（本轮删掉的正是这四份抄件）"
        )
