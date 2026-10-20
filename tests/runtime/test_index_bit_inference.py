# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``0x052D`` 指数位的推断与显式覆盖（2026-10-13 指数 K 线回归的固化）。

这一层测的是**旋钮怎么被送到解码器**，不是解码本身（解码在
``tests/protocol/test_index_bars_layout.py``）：

* :meth:`DirectProviderExecutor._index_bit` 按 :func:`atst.universe.classify_with_index`
  的段表推断，``options["index"]`` 可显式覆盖；
* 门面 :meth:`Client.bars` 暴露 ``index=`` 并折进 options 袋——历史缺陷正在这里：
  底层 ``TdxClient.bars`` 一直有 ``index`` 形参，而门面没有，于是**通过主入口取指数
  日线必然错位**；
* 段表与推断口径不得分家：``ASSET_CLASSES`` 里 ``index_bit`` 是唯一事实源。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.client.api import Client
from atst.errors import ValidationError
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.runtime.executor import DirectProviderExecutor
from atst.runtime.kernel import UnifiedRuntime
from atst.universe import ASSET_CLASSES, classify_with_index

pytestmark = pytest.mark.unit


def _plan(symbol: str, **kwargs: Any) -> Any:
    kwargs.setdefault("count", 5)
    return QueryPlanner().compile(QuerySpec.build("bars", symbols=symbol, **kwargs))


# --------------------------------------------------------------------------- #
# 推断：段表是唯一事实源
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "symbol",
    [
        "sh000001",  # 上证指数
        "sh000300",  # 沪深300
        "sh000688",  # 科创50
        "sz399001",  # 深证成指
        "sz399006",  # 创业板指
    ],
)
def test_index_symbols_infer_the_index_bit(symbol: str) -> None:
    assert DirectProviderExecutor._index_bit(_plan(symbol)) is True


@pytest.mark.parametrize(
    "symbol",
    [
        "sh600519",  # A 股
        "sz000001",  # 深 A——与 sh000001 只差一个前缀，正是撞码那一对
        "sh510300",  # ETF：不是指数
        "sh501018",  # LOF
        "sh110031",  # 可转债
        "sh900901",  # B 股
        "bj920000",  # 北交所
    ],
)
def test_non_index_symbols_do_not(symbol: str) -> None:
    assert DirectProviderExecutor._index_bit(_plan(symbol)) is False


@pytest.mark.parametrize("item", ASSET_CLASSES, ids=lambda item: item.name)
def test_every_asset_class_bit_matches_its_own_table_entry(item: Any) -> None:
    """段表里的 ``index_bit`` 与执行器推断必须同源——改表即改行为，不能有两份答案。"""
    sample = f"{item.tdx_segments[0]}001"
    assert classify_with_index(sample) == (item.name, item.index_bit)
    assert DirectProviderExecutor._index_bit(_plan(sample)) is item.index_bit


# --------------------------------------------------------------------------- #
# 显式覆盖
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("symbol", "override"),
    [
        ("sh000001", False),  # 段表判指数、调用方说不是
        ("sh600519", True),  # 段表判股票、调用方说是
        ("sh000001", True),
        ("sh600519", False),
    ],
)
def test_explicit_override_wins(symbol: str, override: bool) -> None:
    plan = _plan(symbol, options={"index": override})
    assert DirectProviderExecutor._index_bit(plan) is override


@pytest.mark.parametrize("bad", ["yes", 1, 0, 1.0, [], {}])
def test_non_bool_override_fails_closed(bad: Any) -> None:
    """``options["index"]`` 不是 bool 就当场拒——静默当成 False 会让指数继续错位。"""
    plan = _plan("sh000001", options={"index": bad})
    with pytest.raises(ValidationError) as caught:
        DirectProviderExecutor._index_bit(plan)
    assert "options['index'] 必须是 bool" in str(caught.value)


def test_missing_option_means_auto_not_false() -> None:
    """没有这个键才走推断；``False`` 是显式声明，两者不能混。"""
    assert "index" not in (_plan("sh000001").spec.options or {})
    assert DirectProviderExecutor._index_bit(_plan("sh000001")) is True


# --------------------------------------------------------------------------- #
# 门面接线
# --------------------------------------------------------------------------- #


class _RecordingExecutor:
    """只记 plan，不回真数据。"""

    def __init__(self) -> None:
        self.plans: list[Any] = []

    def execute(self, plan: Any) -> QueryResult[Any]:
        self.plans.append(plan)
        return QueryResult.from_plan(
            [{"code": plan.spec.symbols[0]}],
            plan=plan,
            provenance=Provenance.direct(plan),
        )


def _recording_client() -> tuple[Client, _RecordingExecutor]:
    executor = _RecordingExecutor()
    return Client(runtime=UnifiedRuntime(executor=executor)), executor


def test_facade_defaults_to_inference_without_an_option() -> None:
    client, executor = _recording_client()
    client.bars("sh000001", count=5)
    options = executor.plans[-1].spec.options or {}
    assert "index" not in options, "缺省不该写死 False——那正是历史缺陷的形状"


@pytest.mark.parametrize(
    ("symbol", "override", "expected"),
    [
        ("sh000001", False, False),
        ("sh000001", True, True),
        ("sh600519", True, True),
        ("sh600519", False, False),
    ],
)
def test_facade_forwards_the_override_into_options(
    symbol: str, override: bool, expected: bool
) -> None:
    client, executor = _recording_client()
    client.bars(symbol, count=5, index=override)
    options = executor.plans[-1].spec.options or {}
    assert options.get("index") is expected


def test_facade_keeps_the_override_when_strict_is_on() -> None:
    """``strict=True`` 会往 options 里并一个 ``strict`` 键——别把 index 挤掉。"""
    client, executor = _recording_client()
    client.bars("sh000001", count=5, index=False, strict=True)
    options = executor.plans[-1].spec.options or {}
    assert options.get("strict") is True
    assert options.get("index") is False


# --------------------------------------------------------------------------- #
# 执行器 → 底层客户端：旋钮真的到达 ``TdxClient.bars``
# --------------------------------------------------------------------------- #


class _FakeTdx:
    """最小 fake TDX 客户端：只记 ``bars`` 收到的关键字参数。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closes = 0

    def bars(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append({"symbol": symbol, **kwargs})
        return []

    def close(self) -> None:
        self.closes += 1


def _wired_client() -> tuple[Client, _FakeTdx]:
    runtime = UnifiedRuntime(vipdoc_root=None)
    fake = _FakeTdx()
    runtime.executor._tdx_client = lambda _timeout: fake  # type: ignore[method-assign]
    return Client(runtime), fake


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("sh000001", True),
        ("sz399001", True),
        ("sh600519", False),
        ("sz000001", False),
    ],
)
def test_executor_passes_the_inferred_bit_to_the_client(symbol: str, expected: bool) -> None:
    client, fake = _wired_client()
    client.bars(symbol, count=3)
    assert fake.calls[-1]["index"] is expected


def test_executor_passes_the_explicit_override_to_the_client() -> None:
    client, fake = _wired_client()
    client.bars("sh000001", count=3, index=False)
    assert fake.calls[-1]["index"] is False
    assert fake.calls[-1]["count"] == 3


def test_executor_closes_the_borrowed_client() -> None:
    client, fake = _wired_client()
    client.bars("sh000001", count=3)
    assert fake.closes == 1
