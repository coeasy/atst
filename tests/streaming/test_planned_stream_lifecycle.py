from __future__ import annotations

import threading

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import SubscriptionError
from tstdx.streaming.planned import PlannedQuoteStream


class FakeService:
    def __init__(self) -> None:
        self.closed = False

    def quotes(self, symbols, *, provider: str):  # noqa: ANN001,ANN201,ARG002
        return [Quote(code=symbol, price=10.0) for symbol in symbols]

    def close(self) -> None:
        self.closed = True


def test_stop_is_terminal_while_slow_dispatch_callback_is_still_alive() -> None:
    service = FakeService()
    entered = threading.Event()
    release = threading.Event()

    def slow_callback(symbol: str, payload: dict) -> None:  # noqa: ARG001
        entered.set()
        release.wait(timeout=1.0)

    stream = PlannedQuoteStream(provider="tencent", service=service)
    stream.subscribe("sh600519", interval=0.01, on_quote=slow_callback)
    stream.start()
    assert entered.wait(timeout=0.5)

    original_dispatch = stream._dispatch_thread
    assert original_dispatch is not None
    stream.stop(timeout=0.01)

    assert original_dispatch.is_alive()
    assert stream._dispatch_thread is original_dispatch
    with pytest.raises(SubscriptionError) as caught:
        stream.start()
    assert caught.value.context["phase"] == "stream_lifecycle"
    assert stream._dispatch_thread is original_dispatch

    release.set()
    stream.stop(timeout=1.0)
    assert not original_dispatch.is_alive()
    assert stream._dispatch_thread is None
    assert service.closed is False


def test_stop_before_start_rejects_future_start_and_subscribe() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)

    stream.stop()

    with pytest.raises(SubscriptionError) as start_error:
        stream.start()
    assert start_error.value.context["phase"] == "stream_lifecycle"

    with pytest.raises(SubscriptionError) as subscribe_error:
        stream.subscribe("sh600519")
    assert subscribe_error.value.context["phase"] == "stream_lifecycle"
    assert service.closed is False
