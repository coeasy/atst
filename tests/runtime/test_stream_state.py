from __future__ import annotations

import asyncio
import threading

import pytest

from tstdx.errors import SubscriptionError
from tstdx.streaming.state import StreamLifecycle, StreamState
from tstdx.streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


def test_lifecycle_is_one_shot_and_fail_closed() -> None:
    life = StreamLifecycle()
    assert life.state is StreamState.CREATED
    assert life.begin_start() is True
    assert life.state is StreamState.RUNNING
    assert life.begin_start() is False

    life.fail("worker died")
    assert life.state is StreamState.FAILED
    assert life.failure_reason == "worker died"
    with pytest.raises(SubscriptionError):
        life.begin_start()
    with pytest.raises(SubscriptionError):
        life.require_subscribable()

    # Cleanup is allowed after failure, but it must never erase the terminal
    # failure state/reason.
    assert life.begin_stop() is True
    assert life.state is StreamState.FAILED
    life.close()
    assert life.state is StreamState.FAILED
    assert life.failure_reason == "worker died"


def test_clean_stop_reaches_closed() -> None:
    life = StreamLifecycle()
    life.begin_start()
    assert life.begin_stop() is True
    assert life.state is StreamState.STOPPING
    life.close()
    assert life.state is StreamState.CLOSED
    assert life.begin_stop() is False


def test_running_without_worker_fails_closed() -> None:
    life = StreamLifecycle()
    life.begin_start()
    with pytest.raises(SubscriptionError, match="fail-closed"):
        life.assert_running_worker(worker_alive=False)
    assert life.state is StreamState.FAILED


def test_stateful_stream_subscription_ids_never_collide_after_unsubscribe() -> None:
    stream = StatefulQuoteStream()
    first = stream.subscribe("sh600519")
    second = stream.subscribe("sz000001")
    stream.unsubscribe(first)
    third = stream.subscribe("sh601318")
    assert len({first, second, third}) == 3
    assert second in stream._subs
    assert third in stream._subs


def test_stateful_stream_rejects_invalid_subscription_before_worker_io() -> None:
    stream = StatefulQuoteStream()
    with pytest.raises(SubscriptionError):
        stream.subscribe([])
    with pytest.raises(SubscriptionError):
        stream.subscribe("sh600519", interval=0)
    with pytest.raises(SubscriptionError):
        stream.subscribe("sh600519", max_queue=-1)


def test_sync_stream_is_terminal_after_stop_without_start() -> None:
    stream = StatefulQuoteStream()
    stream.stop()
    assert stream.state is StreamState.CLOSED
    with pytest.raises(SubscriptionError):
        stream.start()
    with pytest.raises(SubscriptionError):
        stream.subscribe("sh600519")


def test_sync_stream_detects_dead_running_worker_without_spawning_second() -> None:
    stream = StatefulQuoteStream()
    stream._lifecycle.begin_start()
    stream._thread = None
    with pytest.raises(SubscriptionError, match="fail-closed"):
        stream.start()
    assert stream.state is StreamState.FAILED
    assert stream._thread is None


def test_sync_start_preserves_process_control_baseexception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = StatefulQuoteStream()

    def interrupt(_thread: threading.Thread) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(threading.Thread, "start", interrupt)
    with pytest.raises(KeyboardInterrupt):
        stream.start()
    assert stream.state is StreamState.FAILED
    assert stream.failure_reason == "worker start failed"


def test_sync_cleanup_does_not_hide_failure() -> None:
    stream = StatefulQuoteStream()
    stream._lifecycle.fail("boom")
    stream.stop()
    assert stream.state is StreamState.FAILED
    assert stream.failure_reason == "boom"


@pytest.mark.asyncio
async def test_async_stream_is_terminal_after_stop_without_start() -> None:
    stream = AsyncStatefulQuoteStream()
    await stream.stop()
    assert stream.state is StreamState.CLOSED
    with pytest.raises(SubscriptionError):
        await stream.start()
    with pytest.raises(SubscriptionError):
        stream.subscribe("sh600519")


@pytest.mark.asyncio
async def test_async_running_done_task_fails_closed() -> None:
    stream = AsyncStatefulQuoteStream()
    stream._lifecycle.begin_start()

    async def done() -> None:
        return None

    task = asyncio.create_task(done())
    await task
    stream._task = task
    with pytest.raises(SubscriptionError, match="fail-closed"):
        await stream.start()
    assert stream.state is StreamState.FAILED


@pytest.mark.asyncio
async def test_async_cleanup_does_not_hide_failure() -> None:
    stream = AsyncStatefulQuoteStream()
    stream._lifecycle.fail("boom")
    await stream.stop()
    assert stream.state is StreamState.FAILED
    assert stream.failure_reason == "boom"
