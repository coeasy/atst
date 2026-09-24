# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""轮询侧的终止护栏（V18 第 22 轮，G28）。

第 1 遍「不存在死循环」查出三类形状，都在这里钉住：

* **interval 没人校验**：:class:`~tstdx.streaming.base.QuoteStream` /
  :class:`~tstdx.streaming.base.AsyncQuoteStream` 的轮询线程按 ``sub.interval`` sleep，
  ``interval=0`` 就是一个不打烊的请求循环。带状态机的门面
  （:mod:`tstdx.streaming.stateful`）早就校验了，基类却没有——两份口径，紧的那份
  护不住走宽口的人。现在校验只剩一处（:func:`tstdx.streaming.base.validate_subscription`），
  门面复用同一份。:class:`~tstdx.streaming.engine.StreamEngine` 同理。
* **失败轮不按策略退避、``max_attempts`` 没人读**：``StreamEngine._run`` 原先失败轮也
  按 ``interval`` 立即重试，而 :class:`~tstdx.streaming.engine.ReconnectPolicy` 的
  ``max_attempts`` / :meth:`~tstdx.streaming.engine.ReconnectPolicy.should_give_up` 全包
  零调用点——一个文档写着「超过则放弃」的旋钮其实没人拧。现在失败轮走退避、达上限即停。
* **``iter_messages`` 把超时当终态**：它的 docstring 说「通道关闭或传输耗尽时停止」，
  实现却在任何一次 ``read()`` 返回 ``None`` 时收口，而超时也返回 ``None``。于是外层
  ``while True``（docstring 亲口建议的用法）里，一次空闲超时会让生成器永久不再产出，
  调用方看不出「没有推送」和「通道已经不给帧了」是两回事。

三条判据都是成对写的：先给护栏本身，再给「护栏看得见那次植入的失效」。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from tstdx.errors import SubscriptionError
from tstdx.streaming.base import AsyncQuoteStream, QuoteStream, Subscription
from tstdx.streaming.engine import QuoteChannel, ReconnectPolicy, StreamEngine, StreamEvent
from tstdx.streaming.push import PushChannel
from tstdx.streaming.stateful import StatefulQuoteStream

# --------------------------------------------------------------------------- #
# 护栏 1：interval 必须大于 0——四个入口同一份口径
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("interval", [0, 0.0, -1.0])
def test_the_sync_base_refuses_a_non_positive_interval(interval: float) -> None:
    """``QuoteStream`` 的轮询线程按它 sleep，0 会退化成忙等。"""
    stream = QuoteStream(runtime=object())
    with pytest.raises(SubscriptionError) as excinfo:
        stream.subscribe("sh600519", interval=interval)
    assert excinfo.value.context.get("interval") == interval, "报错没带上那个值"


@pytest.mark.parametrize("interval", [0, -0.5])
def test_the_async_base_refuses_a_non_positive_interval(interval: float) -> None:
    stream = AsyncQuoteStream(runtime=object())
    with pytest.raises(SubscriptionError):
        stream.subscribe("sh600519", interval=interval)


def test_subscription_itself_carries_the_guard() -> None:
    """护栏在数据对象上，不在某一个入口里：新增构造点也躲不过。"""
    with pytest.raises(SubscriptionError):
        Subscription(symbols=["sh600519"], interval=0.0, diff_only=False, max_queue=1024)
    assert (
        Subscription(symbols=["sh600519"], interval=0.01, diff_only=False, max_queue=1024).interval
        == 0.01
    )


def test_the_stateful_facade_shares_the_one_validator() -> None:
    """门面与基类必须查同一份口径（此前是两份手抄的校验）。"""
    from tstdx.streaming import base as base_mod
    from tstdx.streaming import stateful

    assert stateful.validate_subscription is base_mod.validate_subscription, (
        "门面又抄了一份校验器——两份里就会有一份漏掉某条规则"
    )
    with pytest.raises(SubscriptionError):
        StatefulQuoteStream().subscribe("sh600519", interval=0)


def test_stream_engine_refuses_a_non_positive_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        StreamEngine(lambda syms: [], ["sh600519"], interval=0)


# --------------------------------------------------------------------------- #
# 护栏 2：失败轮退避、达到 max_attempts 就收口
# --------------------------------------------------------------------------- #


class _AlwaysFails:
    """注入给 :class:`StreamEngine` 的假轮询：每次都抛，并数被调了几次。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, symbols: Any) -> list[dict[str, Any]]:
        self.calls += 1
        raise RuntimeError("host down")


def test_a_failing_channel_backs_off_instead_of_repolling_at_the_interval() -> None:
    """失败轮等的是策略给的退避，不是轮询间隔。

    复现方式是把两个值拉开量级：``interval`` 极小、``base`` 极大。若失败轮仍按
    ``interval`` 重试，给定墙钟时间内就会打出远超 ``max_attempts`` 的调用。
    """
    poll = _AlwaysFails()
    policy = ReconnectPolicy(base=3600.0, cap=7200.0, max_attempts=50, jitter=False)
    engine = StreamEngine(poll, ["sh600519"], interval=0.001, reconnect=policy)
    errors: list[StreamEvent] = []
    engine.subscribe(lambda ev: errors.append(ev))
    engine.start()
    try:
        time.sleep(0.2)
        # 一轮失败后进入 1 小时退避：这 0.2 秒里只可能有一轮。
        assert poll.calls == 1, f"失败轮仍在按 interval 忙重试：{poll.calls} 次"
        assert policy.attempts == 1
    finally:
        engine.stop()
    assert [ev.kind for ev in errors] == ["error"], "失败没派发 error 事件"


def test_give_up_stops_the_polling_thread() -> None:
    """``max_attempts`` 必须有人读：到数即停，而不是无限重连。"""
    poll = _AlwaysFails()
    policy = ReconnectPolicy(base=0.001, cap=0.002, max_attempts=3, jitter=False)
    engine = StreamEngine(poll, ["sh600519"], interval=0.001, reconnect=policy)
    engine.start()
    deadline = time.monotonic() + 2.0
    thread = engine._thread
    assert thread is not None
    while thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not thread.is_alive(), (
        f"轮询线程未在 max_attempts=3 后退出（已轮询 {poll.calls} 次）——"
        "「超过则放弃」又变成了没人读的文档"
    )
    engine.stop()
    assert poll.calls <= 4, f"收口后还在继续轮询：{poll.calls} 次"


def test_current_delay_does_not_double_count_a_failure() -> None:
    """``fail()`` 记一次，``current_delay()`` 只读不算——两份计数会跳得比声明更快。"""
    policy = ReconnectPolicy(base=1.0, cap=100.0, jitter=False)
    policy.fail()
    assert policy.current_delay() == 2.0
    assert policy.attempts == 1, "current_delay 偷加了失败计数"
    assert policy.next_delay() == 2.0
    assert policy.attempts == 2, "next_delay 该加一次计数"


def test_the_channel_exposes_what_the_driver_needs() -> None:
    """``last_failed`` / ``reconnect`` 是 ``_run`` 读的两格，取错形状当场红。"""
    channel = QuoteChannel(lambda syms: [], ["sh600519"])
    assert channel.last_failed is False
    channel.tick()
    assert channel.last_failed is False
    failing = QuoteChannel(_AlwaysFails(), ["sh600519"], reconnect=ReconnectPolicy(base=0.1))
    failing.tick()
    assert failing.last_failed is True, "驱动侧读不到失败位，就退化成按 interval 忙重试"
    assert isinstance(failing.reconnect, ReconnectPolicy)


# --------------------------------------------------------------------------- #
# 护栏 3：推送通道的「这一轮没有帧」分得清能不能继续
# --------------------------------------------------------------------------- #


class _ScriptedTransport:
    """按脚本产出：bytes=帧、``None``=EOF、``TimeoutError``=空闲、``OSError``=断线。"""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.sent: list[tuple[int, bytes]] = []

    def send_command(self, cmd: int, body: bytes) -> bytes:
        self.sent.append((cmd, body))
        return b"ok"

    def read_frame(self, timeout: float = 5.0) -> Any:
        if not self.script:
            return None
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _frame(code: bytes = b"600519") -> bytes:
    return bytes([1]) + code + b"\x00" * 32


def test_idle_timeouts_do_not_end_the_iteration() -> None:
    """空闲超时不是终态：文档写着「通道关闭或传输耗尽时停止」，就得真的只在那两种停下。"""
    ch = PushChannel(
        _ScriptedTransport([TimeoutError("idle"), _frame(), TimeoutError("idle"), _frame(), None])
    )
    frames = list(ch.iter_messages(timeout=0.001))
    assert len(frames) == 2, f"超时被当成了终态，生成器提前收口：{len(frames)} 帧"


def test_terminal_reasons_end_the_iteration_and_do_not_spin() -> None:
    """EOF / 断线 / 已关闭三种终止都会收口——外层 ``while True`` 因此是有界的。"""
    assert len(list(PushChannel(_ScriptedTransport([None])).iter_messages(timeout=0.001))) == 0
    dead = PushChannel(_ScriptedTransport([OSError("reset")]))
    assert list(dead.iter_messages(timeout=0.001)) == []
    assert isinstance(dead.last_error, OSError), "断线只被伪装成「无帧」，调用方拿不到原因"
    closed = PushChannel(_ScriptedTransport([_frame()]))
    closed.close()
    assert list(closed.iter_messages(timeout=0.001)) == []


def test_read_keeps_its_public_shape() -> None:
    """``read()`` 仍按老口径返回帧或 None——护栏加在内部的原因位上，不改公开契约。"""
    ch = PushChannel(_ScriptedTransport([TimeoutError("idle"), _frame(), None]))
    assert ch.read(timeout=0.001) is None
    assert ch.read(timeout=0.001) is not None
    assert ch.read(timeout=0.001) is None


def test_the_guard_sees_a_planted_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    """正控：把「超时也算终止」改回去，上面那两条判据必须红一条。"""
    terminal = set(PushChannel._TERMINAL_READ)
    assert "timeout" not in terminal, "超时又成了终态——docstring 与实现分叉"
    monkeypatch.setattr(PushChannel, "_TERMINAL_READ", terminal | {"timeout"})
    ch = PushChannel(_ScriptedTransport([TimeoutError("idle"), _frame(), None]))
    assert list(ch.iter_messages(timeout=0.001)) == [], "植入了回归却没被这条判据的形状抓到"
    monkeypatch.undo()
    assert len(list(PushChannel(_ScriptedTransport([_frame(), None])).iter_messages())) == 1
