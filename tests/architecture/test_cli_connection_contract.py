# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""CLI 连接参数契约：``--host`` / ``--timeout`` 必须真的到达执行面。

Phase 6 之后配置面只有内核一个读者（``[hosts] servers`` / ``[core] timeout``）。
CLI 因此只有两种合法姿态：把用户显式说的转下去，或者保持 ``None`` 让内核去读配置。
第三种——解析了却不消费（幻影开关）、或自带字面默认值（遮蔽配置）——由本门禁挡住。
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any

import pytest

from tstdx.cli import runtime_commands
from tstdx.cli._common import _client_kwargs, _transport_kwargs, _transport_timeout
from tstdx.cli.parser import build_parser
from tstdx.config.schema import Config, CoreConfig, HostsConfig

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

#: 一个命令"消费"连接参数的全部合法写法（``_resolve_hosts`` 只覆盖 ``--host``，
#: 用于 handler 自己已经单独取 timeout 的情形）。
_CONNECTION_CONSUMERS = ("_client_kwargs", "_transport_kwargs", "_transport_timeout", "_resolve_hosts")


def _query_result() -> Any:
    """可被 ``serialize_result`` 接受的最小 QueryResult 替身。"""
    provenance = SimpleNamespace(
        kind=SimpleNamespace(value="direct"),
        observed_at_ns=0,
        cache_tier=None,
        fallback=False,
        requested_provider="tdx",
    )
    meta = SimpleNamespace(
        provider="tdx",
        channel="quotation",
        capability="quotes",
        fingerprint="0" * 16,
        provenance=provenance,
    )
    return SimpleNamespace(data=[{"code": "sh600519", "name": "测试"}], meta=meta)


class _Recorder:
    """记录 ``Client`` 构造参数与随后被调用的方法名。"""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.calls: list[str] = []

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
            return _query_result()

        return call


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> _Recorder:
    recorder = _Recorder()
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
    cfg = Config(core=CoreConfig(timeout=1.25))
    monkeypatch.setattr("tstdx.config.get_config", lambda: cfg)
    seen: dict[str, Any] = {}

    class _Stream:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def subscribe(self, *_: Any, **__: Any) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

    import tstdx.streaming as streaming_pkg

    monkeypatch.setattr(streaming_pkg, "QuoteStream", _Stream)
    args = build_parser().parse_args(["stream", "sh600519", "--provider", "tdx", "--seconds", "0"])
    runtime_commands.cmd_stream(args)

    assert seen["provider"] == "tdx"
    assert seen["timeout"] == 1.25


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


def test_no_command_declares_a_connection_option_it_does_not_consume() -> None:
    """F-27 的结构性守卫：解析了 ``--host`` / ``--timeout`` 就必须转下去。

    逐命令断言只能覆盖已知的命令；新增命令若复刻"幻影开关"（CLI 收下参数、
    handler 从不消费），用户照抄即得到静默失效。此门禁让这类命令在合入前就红。
    """
    import inspect

    subparsers = build_parser()._subparsers._group_actions[0]  # type: ignore[union-attr]
    offenders: list[str] = []
    for name, sub in subparsers.choices.items():
        declared = {opt for action in sub._actions for opt in action.option_strings}
        if not declared & {"--host", "--timeout"} or name in DIAGNOSTIC_COMMANDS:
            continue
        func = sub.get_default("func")
        assert callable(func), f"{name} 声明了连接参数却没有 func，无法审计消费面"
        if not any(token in inspect.getsource(func) for token in _CONNECTION_CONSUMERS):
            offenders.append(name)
    assert offenders == []
