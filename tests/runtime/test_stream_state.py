from __future__ import annotations

import asyncio
import threading
import time

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


class _OrderedRuntime:
    """记录"取数 → 关池"真实次序的假内核。

    P2-F 清偿后 ``AsyncQuoteStream.stop()`` 的两条口径都落在这份次序上：收尾排在
    在飞的那次取数**之后**（不能把池关在自己还在用的 worker 底下），以及调用方取消
    不打断这段排空（否则末两次事件的次序就是"池先关、取数后跑完"）。
    """

    def __init__(self, delay: float = 0.4) -> None:
        self.delay = delay
        self.events: list[str] = []

    def quotes(self, symbols: object, **kw: object) -> list[object]:
        del symbols, kw
        self.events.append("poll")
        time.sleep(self.delay)
        self.events.append("poll_done")
        return []

    def close(self) -> None:
        self.events.append("closed")


def _owning_stream(runtime: _OrderedRuntime) -> AsyncStatefulQuoteStream:
    """把假内核挂成"这条流自己拥有的"那份：只有 owns 时停机路径才会去关它。"""
    stream = AsyncStatefulQuoteStream()
    stream._runtime = runtime
    return stream


@pytest.mark.asyncio
async def test_async_stop_closes_the_owned_runtime_after_the_last_poll() -> None:
    runtime = _OrderedRuntime()
    stream = _owning_stream(runtime)
    stream.subscribe("sh600519", interval=0.05)
    await stream.start()
    await asyncio.sleep(0.05)

    await stream.stop()

    assert stream.state is StreamState.CLOSED
    assert runtime.events[0] == "poll", runtime.events
    assert runtime.events[-1] == "closed", runtime.events
    assert stream._task is None


@pytest.mark.asyncio
async def test_async_stop_propagates_the_callers_cancellation_after_draining() -> None:
    """取消不许被吞，也不许换掉收尾次序（第 29 轮 P2-F 清偿的那两条）。

    旧实现在 ``await asyncio.shield(task)`` 外面套 ``suppress(CancelledError)``：调用方
    取消到这里就消失，``_task`` 在 worker 还活着时被清空，而 owned runtime 当场被关
    ——它正在用的那份池。这条判据盯的就是这三件事。
    """
    runtime = _OrderedRuntime()
    stream = _owning_stream(runtime)
    stream.subscribe("sh600519", interval=0.05)
    await stream.start()
    await asyncio.sleep(0.05)

    async def stopper() -> None:
        await stream.stop()

    outer = asyncio.create_task(stopper())
    await asyncio.to_thread(time.sleep, 0.05)  # 让 stop() 置位 _stop 并停在 shield 上
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    assert runtime.events == ["poll", "poll_done", "closed"], runtime.events
    assert stream._task is None
    assert stream.state is StreamState.CLOSED

    #: 终止态是幂等的：收尾已经做完，再来一次不许再关一遍池。
    await stream.stop()
    assert runtime.events.count("closed") == 1
