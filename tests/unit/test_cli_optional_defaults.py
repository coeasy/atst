"""CLI 处理器「可选参数默认值」与 argparse 默认值的防漂移哨兵。

背景：``runtime_commands`` 里的处理器对可选字段一律走 ``getattr(args, "x", DEFAULT)``
而不是裸 ``args.x``——处理器不该因为调用方（内部复用、测试替身、老版本 namespace）少塞
一个可选字段就整条挂掉。代价是默认值在**两处**存在（``atst/cli/parser.py`` 的 argparse
``default=`` 与处理器的 ``getattr`` 兜底）。两处一旦漂移，真实 CLI 走 argparse 一侧、
非 CLI 调用走兜底一侧，同一指令两条路径给出不同结果——正是本仓库反复踩的「同一件事两个
事实源」类问题。

这组用例把两处逐字对齐，任何一边改默认值而另一边没跟上，这里立刻红。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.cli import runtime_commands as rc
from atst.cli.parser import build_parser

pytestmark = pytest.mark.unit


def _argparse_defaults(argv: list[str]) -> dict[str, Any]:
    """跑一遍真实 argparse，拿到这条子命令的完整默认值字典。"""
    return vars(build_parser().parse_args(argv))


class _RecordingApi:
    """只记录透传 kwargs 的门面替身（不校验 provider 是否真支持该能力）。"""

    calls: list[dict[str, Any]] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    def __enter__(self) -> _RecordingApi:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def close(self) -> None:
        return None

    def adjusted_bars(self, symbol: str, **kw: Any) -> list[dict[str, Any]]:
        type(self).calls.append(kw)
        return [{"symbol": symbol}]

    def daily_enriched(self, symbol: str, **kw: Any) -> list[dict[str, Any]]:
        type(self).calls.append(kw)
        return [{"symbol": symbol}]


@pytest.fixture()
def recording(monkeypatch: pytest.MonkeyPatch) -> type[_RecordingApi]:
    import atst.client.api as client_api_mod

    _RecordingApi.calls = []
    monkeypatch.setattr(client_api_mod, "Client", _RecordingApi)
    return _RecordingApi


def _run(func: Any, argv: list[str]) -> dict[str, Any]:
    """构造「只有 argparse 会填的字段」的 namespace 跑处理器，检查兜底值。

    刻意**不**用 ``_argparse_defaults`` 的结果：那样子命令的所有键都在，``getattr``
    兜底永远走不到，这条用例就测了个寂寞。这里只塞 ``json``/``timeout`` 和一个
    ``symbol``，逼处理器用它自己的兜底默认值。
    """
    import argparse

    args = argparse.Namespace(symbol=argv[0], json=True, timeout=None, host=None)
    assert func(args) == 0
    return args


class TestAdjustedBarsDefaults:
    def test_bare_namespace_falls_back_like_argparse(self, recording: type[_RecordingApi]) -> None:
        from atst.cli import main as cli_main

        # --json 走 JSON 出口，不会碰表格渲染；符号给一个不解析的串也无所谓，
        # 因为门面已被替身接管。
        rc_code = cli_main(["adjusted-bars", "sh600519", "--json"])
        assert rc_code == 0
        got = recording.calls[0]
        defaults = _argparse_defaults(["adjusted-bars", "sh600519"])
        assert got["method"] == defaults["method"]
        assert got["period"] == defaults["period"]
        assert got["count"] == defaults["count"]
        assert got["start"] == defaults["start"]
        assert got["provider"] == defaults["provider"]
        assert got["event_source"] == defaults["event_source"]
        # argparse 默认 anchor_date=None → 不传；处理器兜底同样不传。
        assert "anchor_date" not in got

    def test_getattr_fallback_matches_argparse_without_cli(
        self, recording: type[_RecordingApi]
    ) -> None:
        """绕开 argparse 直接调处理器，兜底默认值必须与 argparse 逐字一致。"""
        _run(rc._cmd_adjusted_bars, ["sh600519"])
        got = recording.calls[0]
        defaults = _argparse_defaults(["adjusted-bars", "sh600519"])
        assert got["method"] == defaults["method"] == "qfq"
        assert got["period"] == defaults["period"] == "day"
        assert got["count"] == defaults["count"] == 320
        assert got["start"] == defaults["start"] == 0
        assert got["provider"] == defaults["provider"] == "tdx"
        assert got["event_source"] == defaults["event_source"] == "eastmoney"


class TestDailyEnrichedDefaults:
    def test_getattr_fallback_matches_argparse(self, recording: type[_RecordingApi]) -> None:
        _run(rc._cmd_daily_enriched, ["sh600519"])
        got = recording.calls[0]
        defaults = _argparse_defaults(["daily-enriched", "sh600519"])
        assert got["count"] == defaults["count"] == 250
        assert got["start"] == defaults["start"] == 0
        assert got["adjust"] == defaults["adjust"] == "none"
        assert got["provider"] == defaults["provider"] == "tdx"
        #: 与 adjusted-bars 同款双旋钮：事件来源也是可选的显式入参。
        assert got["event_source"] == defaults["event_source"] == "eastmoney"
