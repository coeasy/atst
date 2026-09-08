from __future__ import annotations

import threading
import time

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import SubscriptionError
from tstdx.streaming.planned import PlannedQuoteStream


class FakeService:
    def __init__(self) -> None:
        self.calls = 0

    def quotes(self, symbols, *, provider: str):  # noqa: ANN001,ANN201
        self.calls += 1
        return [Quote(code=symbol, price=10.0) for symbol in symbols]

    def close(self) -> None:
        pass


def test_start_is_idempotent_while_running_and_does_not_spawn_second_dispatcher() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe("sh600519", interval=0.02)
    assert stream.start() is stream
    poll = stream._poll_thread
    dispatch = stream._dispatch_thread
    try:
        assert stream.start() is stream
        assert stream._poll_thread is poll
        assert stream._dispatch_thread is dispatch
    finally:
        stream.stop(timeout=1.0)


def test_stop_is_terminal_and_rejects_restart_and_new_subscription() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe("sh600519", interval=0.02)
    stream.start()
    stream.stop(timeout=1.0)

    with pytest.raises(SubscriptionError) as restart:
        stream.start()
    assert restart.value.context["phase"] == "stream_lifecycle"

    with pytest.raises(SubscriptionError) as subscribe:
        stream.subscribe("sz000001", interval=1.0)
    assert subscribe.value.context["phase"] == "stream_lifecycle"


def test_slow_callback_surviving_stop_timeout_cannot_create_second_dispatcher() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_callback(symbol: str, payload: dict) -> None:  # noqa: ARG001
        entered.set()
        release.wait(1.0)

    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe(
        "sh600519",
        interval=0.01,
        max_queue=8,
        on_quote=slow_callback,
    )
    stream.start()
    assert entered.wait(0.5)
    dispatch = stream._dispatch_thread

    stream.stop(timeout=0.01)
    assert dispatch is not None
    assert dispatch.is_alive()
    with pytest.raises(SubscriptionError):
        stream.start()
    assert stream._dispatch_thread is dispatch

    release.set()
    deadline = time.monotonic() + 1.0
    while dispatch.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.stop(timeout=1.0)
    assert not dispatch.is_alive()
