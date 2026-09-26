# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""CLI 连接参数契约：``--host`` / ``--timeout`` 必须真的到达执行面。

Phase 6 之后配置面只有内核一个读者（``[hosts] servers`` / ``[core] timeout``）。
CLI 因此只有两种合法姿态：把用户显式说的转下去，或者保持 ``None`` 让内核去读配置。
第三种——解析了却不消费（幻影开关）、或自带字面默认值（遮蔽配置）——由本门禁挡住。
最后一条守卫不限于连接参数：parser 声明的任何选项都必须被 handler（或三个助手）读到。
另有三条结构性守卫：Web 数据命令须经 ``Client`` 执行，且服务面源码不得直接 import
``tstdx.web``（F-29）、不得自建流（F-56，import ``tstdx.streaming`` /
``tstdx.stream_contract`` 即为红）——服务面只翻译，执行入口只有内核一个。
"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tstdx.cli import main, runtime_commands
from tstdx.cli._common import _client_kwargs, _transport_kwargs, _transport_timeout
from tstdx.cli.parser import build_parser
from tstdx.config.schema import Config, CoreConfig, HostsConfig
from tstdx.providers import PROVIDERS
from tstdx.result import ResultMeta

ROOT = Path(__file__).resolve().parents[2]

#: 走 ``Client``（内核）的命令；``--host`` 必须在这些命令上生效。
CLIENT_COMMANDS = (
    ["quotes", "sh600519"],
    ["bars", "sh600519"],
    ["snapshot", "sh600519"],
    ["minute", "sh600519"],
    ["trades", "sh600519"],
    ["security-count"],
    ["security-list"],
)

#: 诊断类命令自带超时字面值是刻意的：它们要遍历候选主站池，不吃 ``[core] timeout``。
DIAGNOSTIC_COMMANDS = {"hosts", "server-test"}


def _query_result(data: Any = None) -> Any:
    """可被 ``serialize_result`` 接受的最小 QueryResult 替身。"""
    provenance = SimpleNamespace(
        kind=SimpleNamespace(value="direct"),
        observed_at_ns=0,
        cache_tier=None,
        fallback=False,
        requested_provider="tdx",
    )
    values: dict[str, Any] = {
        "provider": "tdx",
        "channel": "quotation",
        "capability": "quotes",
        "fingerprint": "0" * 16,
        "provenance": provenance,
        "warnings": (),
    }
    #: 替身按 ``ResultMeta`` 声明的字段逐个成型：生产新增字段而这里没给取值，当场
    #: ``KeyError`` 指名，而不是等 serializer 在别的测试里才炸（F-28"替身比生产窄"）。
    meta = SimpleNamespace(**{item.name: values[item.name] for item in fields(ResultMeta)})
    return SimpleNamespace(
        data=[{"code": "sh600519", "name": "测试"}] if data is None else data, meta=meta
    )


class _Recorder:
    """记录 ``Client`` 构造参数与随后被调用的方法名。"""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.calls: list[str] = []
        #: 命令的表格打印按列取值，故被测命令需要自己的载荷形状。
        self.data: Any = None

    def factory(self, **kwargs: Any) -> _Recorder:
        self.kwargs = kwargs
        return self

    def __enter__(self) -> _Recorder:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return _query_result(self.data)

        return call


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], *, data: Any = None) -> _Recorder:
    recorder = _Recorder()
    recorder.data = data
    # ``cmd_*`` 用模块全局的 Client，``_ClientRows`` 在函数内从 api 模块取——两处都要换。
    monkeypatch.setattr(runtime_commands, "Client", recorder.factory)
    monkeypatch.setattr("tstdx.client.api.Client", recorder.factory)
    args = build_parser().parse_args(argv)
    assert args.func(args) == 0
    assert recorder.kwargs is not None
    return recorder


@pytest.mark.parametrize("argv", CLIENT_COMMANDS)
def test_explicit_host_reaches_the_kernel(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    recorder = _run(monkeypatch, [*argv, "--host", "127.0.0.1:7709"])

    hosts = recorder.kwargs["hosts"]
    assert [(entry.host, entry.port) for entry in hosts] == [("127.0.0.1", 7709)]


@pytest.mark.parametrize("argv", CLIENT_COMMANDS)
def test_absent_host_stays_with_the_config_surface(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    assert _run(monkeypatch, argv).kwargs["hosts"] is None


def test_timeout_flag_is_forwarded_and_unset_defers_to_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _run(monkeypatch, ["margin", "sh600519", "--timeout", "3"]).kwargs["timeout"] == 3.0
    assert _run(monkeypatch, ["margin", "sh600519"]).kwargs["timeout"] is None


def test_no_business_command_defaults_its_timeout_to_a_literal() -> None:
    parser = build_parser()
    subparsers = parser._subparsers._group_actions[0]  # type: ignore[union-attr]
    offenders: list[str] = []
    for name, sub in subparsers.choices.items():
        if name in DIAGNOSTIC_COMMANDS:
            continue
        for action in sub._actions:
            if action.dest == "timeout" and action.default is not None:
                offenders.append(f"{name}: --timeout default={action.default!r}")
    assert offenders == []


def test_raw_transport_command_reads_hosts_and_timeout_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = Config(core=CoreConfig(timeout=2.5), hosts=HostsConfig(servers=[["10.0.0.1", 7709]]))
    monkeypatch.setattr("tstdx.config.get_config", lambda: cfg)
    captured: dict[str, Any] = {}

    class _Raw:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _Raw:
            return self

        def __exit__(self, *exc: Any) -> bool:
            return False

        def block_quotes(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return []

    import tstdx.client as client_pkg

    monkeypatch.setattr(client_pkg, "TdxClient", _Raw)
    args = build_parser().parse_args(["blocks", "0"])
    assert runtime_commands._cmd_blocks(args) == 0
    assert captured == {"hosts": [["10.0.0.1", 7709]], "timeout": 2.5}


def test_stream_forwards_provider_and_connection_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """``stream`` 与其余数据命令同路：连接参数进 ``Client``，流式选项进 ``Client.stream``。

    旧形状自建 ``QuoteStream`` 并让它惰性 new 自己的 runtime，于是 ``--provider``
    从不经过 ``StreamPlanner`` 的 fail-closed 判定，``--host`` 更根本没有声明（F-56）。
    """
    seen: dict[str, Any] = {}

    class _Stream:
        def start(self) -> _Stream:
            return self

        def stop(self) -> None:
            return None

    class _StreamClient:
        def __init__(self, **kwargs: Any) -> None:
            seen["client"] = kwargs

        def __enter__(self) -> _StreamClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            seen["closed"] = True

        def stream(self, symbols: Any, **kwargs: Any) -> _Stream:
            seen["stream"] = {"symbols": list(symbols), **kwargs}
            #: 走一次回调，令退出码落在"收到数据 → 0"这一支。
            kwargs["on_quote"]("sh600519", {"price": 1.0, "last_close": 1.0, "volume": 1})
            return _Stream()

    monkeypatch.setattr(runtime_commands, "Client", _StreamClient)
    args = build_parser().parse_args(
        [
            "stream",
            "sh600519",
            "--host",
            "127.0.0.1:7709",
            "--provider",
            "tdx",
            "--interval",
            "0.5",
            "--diff-only",
            "--max-queue",
            "32",
            "--timeout",
            "1.25",
            "--seconds",
            "0.01",
        ]
    )
    assert runtime_commands.cmd_stream(args) == 0

    hosts = seen["client"]["hosts"]
    assert [(entry.host, entry.port) for entry in hosts] == [("127.0.0.1", 7709)]
    assert seen["client"]["timeout"] == 1.25
    assert seen["stream"]["symbols"] == ["sh600519"]
    assert seen["stream"]["provider"] == "tdx"
    assert (seen["stream"]["interval"], seen["stream"]["diff_only"]) == (0.5, True)
    assert seen["stream"]["max_queue"] == 32
    assert seen.get("closed") is True


def test_stream_command_refuses_a_provider_the_stream_contract_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI 与库面对同一个 ``--provider`` 必须同答案：注册表支持 ``quotes`` ≠ 支持 stream。

    ``tencent`` 是刻意选的：它在注册表里确实支持 ``quotes`` 轮询（``PROVIDERS.supports``），
    所以旁路时代它能在 CLI 上悄悄起流。此处不替换任何对象，走真实 ``Client``；禁网两拦
    是给"万一 fail-closed 又漏了"准备的兜底——那时应当是拒绝，而不是真去连主站。
    判据落在信封文案上：任何一条别的 exit 2 都不算通过。
    """
    import socket

    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("用例内禁止触网")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    assert PROVIDERS.supports("tencent", "quotes")
    # ``--seconds 1`` 而不是 0：0 现在在 CLI 门口就被 :func:`cmd_stream` 判死（E1010），
    # 那样这条用例测的是"窗口长度不合法"，不是它声称的"Provider 不承载流式"。
    assert main(["stream", "sh600519", "--provider", "tencent", "--seconds", "1"]) == 2
    assert "只存在 tdx Direct stream binding" in capsys.readouterr().err
    #: 同一支命令、同一个 ``--seconds``，非法窗口要单独给出它自己的那句话。
    assert main(["stream", "sh600519", "--provider", "tdx", "--seconds", "0"]) == 2
    assert "--seconds 必须为正数" in capsys.readouterr().err


def test_helpers_keep_explicit_flags_ahead_of_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Config(core=CoreConfig(timeout=9.0), hosts=HostsConfig(servers=[["10.0.0.1", 7709]]))
    monkeypatch.setattr("tstdx.config.get_config", lambda: cfg)
    args = argparse.Namespace(host=["1.2.3.4:5555"], timeout=1.0)

    assert [(e.host, e.port) for e in _transport_kwargs(args)["hosts"]] == [("1.2.3.4", 5555)]
    assert _transport_kwargs(args)["timeout"] == 1.0
    assert _transport_timeout(args) == 1.0
    assert [(e.host, e.port) for e in _client_kwargs(args)["hosts"]] == [("1.2.3.4", 5555)]
    assert _client_kwargs(args)["timeout"] == 1.0

    empty = argparse.Namespace(host=[], timeout=None)
    assert _transport_kwargs(empty) == {"hosts": [["10.0.0.1", 7709]], "timeout": 9.0}
    assert _client_kwargs(empty) == {"hosts": None, "timeout": None}


def test_no_command_declares_an_option_it_does_not_consume() -> None:
    """F-27/F-28 的结构性守卫：parser 收下每个选项，handler 就必须消费它。

    逐命令断言只能覆盖已知的命令；新增命令若复刻"幻影开关"（CLI 收下参数、
    handler 从不消费），用户照抄即得到静默失效。此门禁不看具体命令，也不看具体
    选项名，故对新增命令同样有效。
    """
    offenders = _dead_cli_options()
    assert offenders == []


def _dead_cli_options() -> list[str]:
    """返回"声明了却没有任何源码读取"的 ``<command> --<option>`` 清单。"""
    import inspect

    from tstdx.cli import _common

    # 只有这四个助手被允许"代 handler 消费参数"，且逐个点名——把整模块源码当作
    # 豁免面会让任意一个 `args.x` 为所有命令开绿灯。
    helper_src = "".join(
        inspect.getsource(getattr(_common, fn))
        for fn in ("_client_kwargs", "_transport_kwargs", "_transport_timeout", "_resolve_hosts")
    )
    subparsers = build_parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
    dead: list[str] = []
    for name, sub in subparsers.choices.items():
        func = sub.get_default("func")
        if not callable(func) or name in DIAGNOSTIC_COMMANDS:
            continue
        src = inspect.getsource(func) + helper_src
        for action in sub._actions:
            dest = action.dest
            if dest in ("func", "help"):
                continue
            if not _is_read(dest, src):
                dead.append(f"{name}: --{action.option_strings[-1].lstrip('-')} (dest={dest})")
    return dead


def _is_read(dest: str, src: str) -> bool:
    return (
        re.search(rf"args\.{re.escape(dest)}\b", src) is not None
        or re.search(rf'getattr\(args,\s*"{re.escape(dest)}"', src) is not None
    )


#: 数据源在 Web 侧、但同样已注册为 capability 的命令。
WEB_CAPABILITY_COMMANDS: tuple[tuple[list[str], str], ...] = (
    (["changes", "--types", "8201"], "stock_changes"),
    (["hot"], "hot_rank"),
)


@pytest.mark.parametrize(("argv", "capability"), WEB_CAPABILITY_COMMANDS)
def test_web_backed_command_calls_the_kernel_not_the_source(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], capability: str
) -> None:
    """Web 数据也必须经 ``Client``：旁路会同时丢掉信封、provenance 与能力校验。"""
    recorder = _run(monkeypatch, argv, data=[])
    assert recorder.calls == [capability]


def test_service_faces_never_import_the_web_layer() -> None:
    """F-29 的结构性守卫：服务面只翻译不执行，数据入口只有 ``Client`` 一个。

    逐命令断言只能看住已知命令；新增命令若直接 ``from ..web.session import …``
    就又开出第二条执行路径。此门禁扫描全部服务面源码（CLI / HTTP / WS / MCP），
    不看具体命令名，故对新增命令同样有效。
    """
    offenders = [
        f"{path}: import {module}"
        for path, module in _service_face_imports()
        if _under(module, "tstdx.web")
    ]
    assert offenders == []


def test_service_faces_never_build_a_stream_themselves() -> None:
    """F-56：流式的第二条执行路径不在 web 层，在 ``tstdx.streaming``。

    服务面一旦自己构造 ``QuoteStream``，就绕开了 ``Client.stream`` 里那次
    ``StreamPlanner.compile``——tdx-only 的 fail-closed 判定、Provider 注册表的
    ``require`` 全部失效，且对象会惰性 new 出自己的 ``UnifiedRuntime``。
    """
    edges = _service_face_imports()
    offenders = [
        f"{path}: import {module}"
        for path, module in edges
        if _under(module, "tstdx.streaming") or _under(module, "tstdx.stream_contract")
    ]
    assert offenders == []


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _service_face_imports() -> list[tuple[str, str]]:
    """四个服务面源码里的每一条 import 边，形如 ``(相对路径, 绝对模块名)``。"""
    faces = (ROOT / "tstdx" / "cli", ROOT / "tstdx" / "integration")
    edges: list[tuple[str, str]] = []
    for face in faces:
        for path in sorted(face.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            package = ".".join(path.relative_to(ROOT).parts[:-1])
            for module in _imported_modules(tree, package=package):
                edges.append((str(path.relative_to(ROOT)), module))
    assert edges, "服务面源码里解析不出任何 import，两条门禁同时失明"
    return edges


def _imported_modules(tree: ast.AST, *, package: str) -> list[str]:
    """把 ``import`` 节点解析成绝对模块名（相对导入按所属包补全）。"""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package
            for _ in range(node.level - 1):
                base = base.rpartition(".")[0]
            names.append(f"{base}.{node.module}" if node.module else base)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names
