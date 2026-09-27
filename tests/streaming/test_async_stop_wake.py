# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G49 — :class:`AsyncQuoteStream` 的每条睡眠腿都必须能被 ``stop()`` 叫醒。

第 31 轮第 2 遍实测到的无界形状：异步流的轮询内核把四处等待写成裸
``await asyncio.sleep(...)``，而停机信号 ``self._stop`` 是一个 :class:`threading.Event`
——异步侧没法 ``await`` 它，于是没有任何一条腿会因它提前结束。后果落在 ``stop()`` 上：

* ``interval`` 只要 ``> 0`` 就合法（:func:`~tstdx.streaming.base.validate_subscription`
  挡的是下界，没有上界），所以一次 ``interval=3600`` 的订阅把停机拖成 3600 秒；
* ``stop()`` 里那圈 ``await asyncio.shield(task)`` 会把调用方的取消记一笔再吞掉，所以
  ``asyncio.wait_for(stop(), 1.0)`` 也管不住它——超时被吞，等的人继续等。

同步孪生一直是对的：它的四条腿全写 :meth:`threading.Event.wait`，``stop()`` 一置位就当条
腿醒来。同一份契约在两张面上一个生效一个不生效，就是第 31 轮 B-3 那一族（同步面与异步面
严格强弱不一）。

实测证据（同轮日志 ``Temp/g47_before.out`` / ``Temp/g47_after.out``，修复前后）：

* 修复前 ``HUNG: stop() outlived a 15s wall guard (caller's 1s timeout never honoured)``；
* 修复后 ``stop() returned after 0.031s wall / 0.000s cpu`` 且
  ``worker task still alive: False`` / ``owned handle cleared: True``。

修复口径：新增 :func:`~tstdx.streaming.base._sleep_or_stop`（按 ``_STOP_POLL_SECONDS``
轮询 ``_stop``，形状对齐同步侧的 ``Event.wait``），四处睡眠全走它。``stop()`` 的墙上界就此
变成"在飞的那一次 ``to_thread`` 取数 + 最多一个轮询节拍"，与同步侧
``stop(timeout=2.0)`` 量的是同一段时间。

判据不看注释也不看分支写法，只看四件事：① 长间隔订阅的停机不被 ``interval`` 顶着走；
② 退避那条腿（``ReconnectPolicy`` 关掉抖动后是确定值）同样被 ``stop()`` 叫醒；③ 现扫两条
内核的睡眠腿：异步侧四条全走 ``_sleep_or_stop``、一条裸 ``asyncio.sleep`` 都不留（把旧写法
种回去必须红），同步孪生四条全是 ``Event.wait``——腿数与形状两两面必须对得上。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import time
from pathlib import Path

import pytest

from tstdx.errors import TdxError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.streaming.base import AsyncQuoteStream, QuoteStream
from tstdx.streaming.engine import ReconnectPolicy

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "tstdx" / "streaming" / "base.py"

#: 一次停机的允许上界。修复前两条腿分别要 3600 秒与 600 秒，这个数只需远小于它们。
STOP_BOUND_SECONDS = 1.0

#: 修好之后真正声称的停机耗时：唤醒粒度 ``_STOP_POLL_SECONDS`` 的几倍，而不是那道守卫。
WAKE_BOUND_SECONDS = 0.5

#: 轮询内核的两个方法：睡眠腿只可能长在这里（``stop()`` 自己的 shield 循环量的是
#: "要不要等 worker"，不是睡眠）。
KERNELS = frozenset({"_run", "_poll_once"})

#: 算作一条睡眠腿的被调物末段：裸睡眠，以及两种 stop 可唤醒的等待。
SLEEPER_TAILS = frozenset({"sleep", "_sleep_or_stop", "wait"})


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base is not None else node.attr
    return None


class _ScriptedRuntime:
    """轮询内核唯一的外部依赖：按脚本返回一行行情，或者按脚本抛错。"""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.calls = 0
        self.closed = False
        self._fail_with = fail_with

    def quotes(self, symbols, **kwargs):  # noqa: ANN001, ANN201 - 替身按内核形状写
        del kwargs
        self.calls += 1
        if self._fail_with is not None:
            raise self._fail_with
        values = [symbols] if isinstance(symbols, str) else list(symbols)
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols=values, provider="tdx", currentness="live")
        )
        return QueryResult.from_plan(
            [{"code": item[-6:], "price": 1.0} for item in values],
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def close(self) -> None:
        self.closed = True


def _callee(call: ast.Call) -> str:
    return _dotted(call.func) or "<expr>"


def _sleep_legs(source: str, class_name: str) -> dict[str, list[str]]:
    """现扫一个类的两条内核：``方法名 -> [每条睡眠腿的被调物]``（按源码顺序）。

    AST 而非子串（G42 同一把尺）：注释里写 ``await asyncio.sleep`` 不算一条腿。
    """
    legs: dict[str, list[tuple[int, str]]] = {}
    tree = ast.parse(source)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name]:
        for fn in [
            n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in KERNELS
        ]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    callee = _callee(node)
                    if callee.split(".")[-1] in SLEEPER_TAILS:
                        legs.setdefault(fn.name, []).append((node.lineno, callee))
    return {name: [callee for _, callee in sorted(items)] for name, items in legs.items()}


def _bare_async_sleeps(source: str) -> list[str]:
    """异步内核里所有裸 ``asyncio.sleep`` 的位置（应当为空）。"""
    out: list[str] = []
    tree = ast.parse(source)
    for cls in [
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AsyncQuoteStream"
    ]:
        for fn in [
            n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in KERNELS
        ]:
            out += [
                f"{cls.name}.{fn.name}#L{node.lineno}"
                for node in ast.walk(fn)
                if isinstance(node, ast.Call) and _callee(node) == "asyncio.sleep"
            ]
    return out


async def _wait_until(predicate, *, limit: float = 2.0) -> bool:  # noqa: ANN001 - 替身谓词
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def _drain(stream: AsyncQuoteStream) -> None:
    """红路子的清场：判据失败时不许留下一条 worker 任务污染事件循环。"""
    task = stream._task
    if task is not None and not task.done():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _seconds_until_stopped(stream: AsyncQuoteStream) -> float:
    """量一次 ``stop()`` 的墙上耗时。

    不用 ``asyncio.wait_for``：修复前 ``stop()`` 会把那次取消记一笔再吞掉（它自己的
    shield 循环），取消打不断它，整个套件就被顶住不放——那一格实测顶穿过 15 秒守卫。
    这里改成"到点上界就自己判红，并且先把 worker 掐掉"，红也要红得收口。
    """
    stop_call = asyncio.create_task(stream.stop())
    started = time.monotonic()
    done, _pending = await asyncio.wait({stop_call}, timeout=STOP_BOUND_SECONDS)
    elapsed = time.monotonic() - started
    if not done:
        await _drain(stream)
        stop_call.cancel()
        with contextlib.suppress(BaseException):
            await stop_call
        pytest.fail(
            f"stop() 顶穿了 {STOP_BOUND_SECONDS}s 上界（已等 {elapsed:.3f}s）："
            "停机被某条睡眠腿攥在手里"
        )
    error = stop_call.exception()
    if error is not None:
        raise error
    return elapsed


@pytest.mark.unit
def test_a_long_interval_subscription_does_not_own_the_stop_clock() -> None:
    """① ``interval=3600`` 的订阅不能把停机拖成 3600 秒。

    修复前实测：``HUNG: stop() outlived a 15s wall guard``（``Temp/g47_before.out``）。
    """

    async def main() -> tuple[float, bool]:
        runtime = _ScriptedRuntime()
        stream = AsyncQuoteStream(runtime=runtime)  # type: ignore[arg-type]
        stream.subscribe("sh600519", interval=3600.0)
        await stream.start()
        assert await _wait_until(lambda: runtime.calls >= 1), "内核没跑过一轮，量不到停机"
        #: 让 worker 确实落进 interval 那条腿，而不是停在取数与打点之间。
        await asyncio.sleep(0.1)
        try:
            elapsed = await _seconds_until_stopped(stream)
        finally:
            await _drain(stream)
        return elapsed, stream._task is None

    elapsed, handle_cleared = asyncio.run(main())
    assert elapsed < WAKE_BOUND_SECONDS, f"停机被 interval 顶着走了 {elapsed:.3f}s"
    assert handle_cleared, "stop() 返回时 worker 句柄还在：停机与 worker 已死不是同一件事"


@pytest.mark.unit
def test_the_backoff_leg_wakes_for_stop() -> None:
    """② 失败退避那条腿同样要 stop 可唤醒——它比 interval 更坏（默认 ``cap`` 30 秒）。

    抖动关掉、基准给 600 秒：修复前这一格要等 600 秒才醒，是四条腿里最长的一格。
    """

    async def main() -> tuple[float, int]:
        runtime = _ScriptedRuntime(fail_with=TdxError("探针：这一轮必失败"))
        stream = AsyncQuoteStream(runtime=runtime)  # type: ignore[arg-type]
        stream._reconnect = ReconnectPolicy(base=600.0, cap=600.0, jitter=False)
        stream.subscribe("sh600519", interval=3600.0)
        await stream.start()
        assert await _wait_until(lambda: runtime.calls >= 1), "失败轮没发生，量不到退避腿"
        await asyncio.sleep(0.1)
        try:
            elapsed = await _seconds_until_stopped(stream)
        finally:
            await _drain(stream)
        return elapsed, runtime.calls

    elapsed, calls = asyncio.run(main())
    assert calls >= 1
    assert elapsed < WAKE_BOUND_SECONDS, f"退避腿没被 stop() 叫醒：等了 {elapsed:.3f}s"


@pytest.mark.unit
def test_every_async_sleep_leg_goes_through_the_stop_aware_helper() -> None:
    """③ 异步内核四条腿，一条不落全走 ``_sleep_or_stop``，裸 ``asyncio.sleep`` 归零。

    四条腿依次是：``_run`` 的未预期异常退避，``_poll_once`` 的空订阅节拍、失败退避、
    interval 打点。腿数也进断言——少一条腿同样是回潮（把 ``stop()`` 的上界交回参数手里）。
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert _bare_async_sleeps(source) == []
    assert _sleep_legs(source, "AsyncQuoteStream") == {
        "_run": ["_sleep_or_stop"],
        "_poll_once": ["_sleep_or_stop", "_sleep_or_stop", "_sleep_or_stop"],
    }


@pytest.mark.unit
def test_planted_bare_sleep_leg_is_caught() -> None:
    """判据自身不是装饰：把旧写法（裸 ``asyncio.sleep``）种回 interval 那条腿，必须被看见。"""
    real = SOURCE.read_text(encoding="utf-8")
    assert _bare_async_sleeps(real) == []
    anchor = "        await _sleep_or_stop(self._stop, min((sub.interval for sub in subs), default=1.0))\n"
    planted = real.replace(
        anchor,
        "        await asyncio.sleep(min((sub.interval for sub in subs), default=1.0))\n",
        1,
    )
    assert planted != real, "种桩锚点没命中，这一格自证失败"
    caught = _bare_async_sleeps(planted)
    assert len(caught) == 1 and caught[0].startswith("AsyncQuoteStream._poll_once"), (
        f"种回去没被抓到：{caught}"
    )
    assert _sleep_legs(planted, "AsyncQuoteStream") == {
        "_run": ["_sleep_or_stop"],
        "_poll_once": ["_sleep_or_stop", "_sleep_or_stop", "asyncio.sleep"],
    }, "种回去之后腿表没跟着变"


@pytest.mark.unit
def test_the_stop_aware_helper_both_polls_the_flag_and_caps_each_sleep() -> None:
    """唤醒粒度读得到：``_sleep_or_stop`` 必须现查 ``_stop``，且单次睡眠钳在常量内。

    只看函数体的行动（G41 口径）：把它改回裸 ``asyncio.sleep(delay)`` 这一格就该红。
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_sleep_or_stop"
    )
    attrs = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    sleeps = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and _callee(node) in {"asyncio.sleep", "sleep"}
    ]
    assert len(sleeps) == 1, f"睡眠只该有一处：{[_callee(n) for n in sleeps]}"
    #: 被钳住的那一次睡眠自己看不到 ``delay`` 这个参数——它必须按唤醒粒度切片。
    bound_names = {n.id for n in ast.walk(sleeps[0]) if isinstance(n, ast.Name)}
    assert "is_set" in attrs, "睡眠不再看停机信号：stop() 只能等它自己走完"
    assert {"monotonic", "min"} <= (attrs | bound_names), "睡眠腿不再按墙钟截止切片"
    assert "_STOP_POLL_SECONDS" in bound_names, "单次睡眠没被钳在唤醒粒度内：上界又变回 delay"


@pytest.mark.unit
def test_sync_twin_keeps_the_same_four_legs_on_the_stop_event() -> None:
    """④ 同步孪生不得跟着搬走：四条腿还是 ``Event.wait``，且行为对照就在这格里。"""
    assert _sleep_legs(SOURCE.read_text(encoding="utf-8"), "QuoteStream") == {
        "_run": ["self._stop.wait"],
        "_poll_once": ["self._stop.wait"] * 3,
    }

    runtime = _ScriptedRuntime()
    stream = QuoteStream(runtime=runtime)  # type: ignore[arg-type]
    stream.subscribe("sh600519", interval=3600.0)
    stream.start()
    deadline = time.monotonic() + 2.0
    while runtime.calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    stream.stop(timeout=STOP_BOUND_SECONDS)
    assert time.monotonic() - started < WAKE_BOUND_SECONDS, "同步面反而被 interval 顶住了"
