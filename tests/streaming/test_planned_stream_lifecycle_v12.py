from __future__ import annotations

import threading
import time

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import SubscriptionError
from tstdx.streaming.planned import PlannedQuoteStream, StreamState


class FakeService:
    def __init__(self) -> None:
        self.calls = 0

    def quotes(self, symbols, *, provider: str):  # noqa: ANN001,ANN201,ARG002
        self.calls += 1
        return [Quote(code=symbol, price=10.0) for symbol in symbols]

    def close(self) -> None:
        pass


def _wait_for_state(stream: PlannedQuoteStream, expected: StreamState) -> None:
    deadline = time.monotonic() + 1.0
    while stream.state is not expected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stream.state is expected


def test_state_machine_created_running_closed_and_start_is_idempotent() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    assert stream.state is StreamState.CREATED

    stream.subscribe("sh600519", interval=0.02)
    assert stream.start() is stream
    assert stream.state is StreamState.RUNNING
    poll = stream._poll_thread
    dispatch = stream._dispatch_thread
    try:
        assert stream.start() is stream
        assert stream.state is StreamState.RUNNING
        assert stream._poll_thread is poll
        assert stream._dispatch_thread is dispatch
    finally:
        stream.stop(timeout=1.0)

    assert stream.state is StreamState.CLOSED


def test_stop_is_terminal_and_rejects_restart_and_new_subscription() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe("sh600519", interval=0.02)
    stream.start()
    stream.stop(timeout=1.0)

    assert stream.state is StreamState.CLOSED
    with pytest.raises(SubscriptionError) as restart:
        stream.start()
    assert restart.value.context["phase"] == "stream_lifecycle"
    assert restart.value.context["state"] == "closed"

    with pytest.raises(SubscriptionError) as subscribe:
        stream.subscribe("sz000001", interval=1.0)
    assert subscribe.value.context["phase"] == "stream_lifecycle"
    assert subscribe.value.context["state"] == "closed"


def test_stop_before_start_moves_created_directly_to_closed() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    assert stream.state is StreamState.CREATED

    stream.stop(timeout=0.1)

    assert stream.state is StreamState.CLOSED
    assert stream._poll_thread is None
    assert stream._dispatch_thread is None


def test_partial_worker_start_failure_fails_closed_and_stop_skips_unstarted_join(
    monkeypatch,
) -> None:  # noqa: ANN001
    original_start = threading.Thread.start
    starts = 0

    def flaky_start(thread: threading.Thread) -> None:
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("dispatch-start-failed")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", flaky_start)
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe("sh600519", interval=0.01)

    with pytest.raises(SubscriptionError) as caught:
        stream.start()

    assert stream.state is StreamState.FAILED
    assert caught.value.context["phase"] == "stream_lifecycle"
    assert caught.value.context["state"] == "failed"
    assert caught.value.context["poll_started"] is True
    assert caught.value.context["dispatch_started"] is False
    assert caught.value.context["cause_type"] == "RuntimeError"

    stream.stop(timeout=1.0)
    assert stream.state is StreamState.CLOSED
    assert stream._poll_thread is None
    assert stream._dispatch_thread is None


def test_slow_callback_exposes_stopping_and_cannot_create_second_dispatcher() -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow_callback(symbol: str, payload: dict[str, object]) -> None:  # noqa: ARG001
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
    assert stream.state is StreamState.STOPPING
    assert dispatch is not None
    assert dispatch.is_alive()
    with pytest.raises(SubscriptionError) as restart:
        stream.start()
    assert restart.value.context["state"] == "stopping"
    assert stream._dispatch_thread is dispatch

    with pytest.raises(SubscriptionError) as subscribe:
        stream.subscribe("sz000001")
    assert subscribe.value.context["state"] == "stopping"

    release.set()
    deadline = time.monotonic() + 1.0
    while dispatch.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.stop(timeout=1.0)
    assert not dispatch.is_alive()
    assert stream.state is StreamState.CLOSED


def test_unexpected_worker_shutdown_fails_closed_and_rejects_new_work() -> None:
    stream = PlannedQuoteStream(provider="tencent", service=FakeService())
    stream.subscribe("sh600519", interval=0.01)
    stream.start()
    assert stream.state is StreamState.RUNNING

    # Simulate a worker termination signal that did not pass through stop().
    # Both workers observe the event; the first finalizer must transition the
    # instance to FAILED instead of leaving a restartable half-dead stream.
    stream._stop.set()
    stream._dispatch_wakeup.set()
    _wait_for_state(stream, StreamState.FAILED)

    with pytest.raises(SubscriptionError) as restart:
        stream.start()
    assert restart.value.context["state"] == "failed"

    with pytest.raises(SubscriptionError) as subscribe:
        stream.subscribe("sz000001")
    assert subscribe.value.context["state"] == "failed"

    stream.stop(timeout=1.0)
    assert stream.state is StreamState.CLOSED
