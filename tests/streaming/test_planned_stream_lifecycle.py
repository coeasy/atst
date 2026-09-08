from __future__ import annotations

import threading
import time
from typing import Any

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


class BlockingService:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def quotes(self, symbols: list[str], *, provider: str) -> list[Quote]:  # noqa: ARG002
        self.entered.set()
        self.release.wait(2.0)
        return [Quote(code=symbol, price=10.0) for symbol in symbols]

    def close(self) -> None:
        self.closed.set()


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


def test_callback_can_stop_stream_without_joining_itself() -> None:
    service = FakeService()
    stopped = threading.Event()
    callback_errors: list[BaseException] = []
    deliveries: list[str] = []
    stream = PlannedQuoteStream(provider="tencent", service=service)

    def on_quote(symbol: str, payload: dict[str, Any]) -> None:  # noqa: ARG001
        deliveries.append(symbol)
        try:
            stream.stop(timeout=0.5)
        except BaseException as exc:
            callback_errors.append(exc)
        finally:
            stopped.set()

    stream.subscribe("sh600519", interval=0.01, on_quote=on_quote)
    stream.start()

    assert stopped.wait(1.0)
    assert callback_errors == []
    assert deliveries == ["sh600519"]
    with pytest.raises(SubscriptionError) as caught:
        stream.start()
    assert caught.value.context["phase"] == "stream_lifecycle"


def test_stop_timeout_drops_late_result_and_poll_finalizer_closes_owned_service() -> None:
    service = BlockingService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    # Injected services are normally externally owned. Flip ownership only to
    # exercise the internal finalizer without opening a real Provider connection.
    stream._owns_service = True
    delivered = threading.Event()

    stream.subscribe(
        "sh600519",
        interval=0.01,
        on_quote=lambda _symbol, _payload: delivered.set(),
    )
    stream.start()
    assert service.entered.wait(1.0)

    stream.stop(timeout=0.01)
    assert service.closed.is_set() is False
    assert delivered.is_set() is False

    service.release.set()
    assert service.closed.wait(1.0)
    time.sleep(0.05)

    assert delivered.is_set() is False
    assert stream._service_closed is True
    assert stream._poll_thread is None
    assert stream._closed is True


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


def test_negative_stop_timeout_is_rejected() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    with pytest.raises(ValueError, match="timeout"):
        stream.stop(timeout=-0.1)
