from __future__ import annotations

import threading
import time

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import SourceUnavailable, SubscriptionError
from tstdx.streaming.planned import PlannedQuoteStream


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.raise_error = False
        self.closed = False
        self._lock = threading.Lock()

    def quotes(self, symbols, *, provider: str):  # noqa: ANN001,ANN201
        with self._lock:
            self.calls.append((provider, tuple(symbols)))
        if self.raise_error:
            raise SourceUnavailable("provider down")
        return [Quote(code=symbol, price=10.0) for symbol in symbols]

    def close(self) -> None:
        self.closed = True


def test_due_subscriptions_union_symbols_without_duplicates() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    stream.subscribe(["sh600519", "sz000001"], interval=1.0)
    stream.subscribe(["sz000001", "sh601318"], interval=5.0)
    subs = stream._subscriptions()
    assert stream._union_symbols(subs) == ["sh600519", "sz000001", "sh601318"]


def test_invalid_subscription_inputs_are_terminal_e6_errors() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    for call in (
        lambda: stream.subscribe([], interval=1.0),
        lambda: stream.subscribe("sh600519", interval=0),
        lambda: stream.subscribe("sh600519", max_queue=0),
        lambda: stream.subscribe("not-a-symbol", interval=1.0),
    ):
        with pytest.raises(SubscriptionError) as caught:
            call()
        assert caught.value.code == "E6010"
        assert caught.value.advice.retryable is False
        assert caught.value.advice.switch_host is False


def test_injected_service_identity_is_preserved() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    assert stream.service is service
    stream.stop()
    assert service.closed is False


def test_advance_due_skips_missed_intervals_without_catchup_loop() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    sub_id = stream.subscribe("sh600519", interval=0.5)
    sub = {item.id: item for item in stream._subscriptions()}[sub_id]
    sub.next_due = 10.0
    stream._advance_due(sub, 12.2)
    assert sub.next_due == 12.5


def test_slow_callback_does_not_block_provider_polling() -> None:
    service = FakeService()
    entered = threading.Event()
    release = threading.Event()

    def slow_callback(symbol: str, payload: dict) -> None:  # noqa: ARG001
        entered.set()
        release.wait(timeout=1.0)

    stream = PlannedQuoteStream(provider="tencent", service=service)
    stream.subscribe(
        "sh600519",
        interval=0.02,
        max_queue=16,
        on_quote=slow_callback,
    )
    stream.start()
    try:
        assert entered.wait(timeout=0.5)
        time.sleep(0.08)
        assert len(service.calls) >= 2
        assert {provider for provider, _ in service.calls} == {"tencent"}
    finally:
        release.set()
        stream.stop(timeout=1.0)


def test_fast_subscription_does_not_force_slow_symbol_into_every_poll() -> None:
    service = FakeService()
    stream = PlannedQuoteStream(provider="tencent", service=service)
    stream.subscribe("sh600519", interval=0.02, max_queue=32)
    stream.subscribe("sz000001", interval=0.10, max_queue=32)
    stream.start()
    try:
        time.sleep(0.14)
    finally:
        stream.stop(timeout=1.0)

    fast = sum("sh600519" in symbols for _, symbols in service.calls)
    slow = sum("sz000001" in symbols for _, symbols in service.calls)
    assert fast > slow
    assert slow <= 3


def test_provider_errors_are_reported_without_provider_switch() -> None:
    service = FakeService()
    service.raise_error = True
    got_error = threading.Event()
    errors: list[Exception] = []

    def on_error(exc: Exception) -> None:
        errors.append(exc)
        got_error.set()

    stream = PlannedQuoteStream(provider="tencent", service=service)
    stream.subscribe("sh600519", interval=0.02, on_error=on_error)
    stream.start()
    try:
        assert got_error.wait(timeout=0.6)
        assert errors
        assert all(provider == "tencent" for provider, _ in service.calls)
    finally:
        stream.stop(timeout=1.0)
