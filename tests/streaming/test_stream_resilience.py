from __future__ import annotations

import asyncio
import threading

import pytest

from tstdx.errors import SubscriptionError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.streaming import (
    AsyncStatefulQuoteStream,
    BackpressureQueue,
    DeltaMerger,
    GapFiller,
    ReconnectPolicy,
    StatefulQuoteStream,
    StreamState,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def quotes(self, symbols, **kwargs):
        del kwargs
        self.calls += 1
        values = [symbols] if isinstance(symbols, str) else list(symbols)
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols=values, provider="tdx", currentness="live")
        )
        return QueryResult.from_plan(
            [{"code": item[-6:], "price": float(self.calls)} for item in values],
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def close(self) -> None:
        self.closed = True


def test_stateful_stream_polls_through_runtime_and_closes_cleanly() -> None:
    runtime = FakeRuntime()
    received: list[tuple[str, dict]] = []
    ready = threading.Event()
    stream = StatefulQuoteStream(runtime=runtime)  # type: ignore[arg-type]
    stream.subscribe(
        "sh600519",
        interval=0.01,
        on_quote=lambda symbol, quote: (received.append((symbol, quote)), ready.set()),
    )
    stream.start()
    assert ready.wait(1.0)
    stream.stop(timeout=1.0)
    assert runtime.calls >= 1
    assert received[0][0] == "sh600519"
    assert received[0][1]["code"] == "600519"
    assert stream.state is StreamState.CLOSED
    assert runtime.closed is False  # externally supplied runtime is not owned by stream


def test_stateful_stream_is_one_shot_after_close() -> None:
    stream = StatefulQuoteStream(runtime=FakeRuntime())  # type: ignore[arg-type]
    stream.subscribe("sh600519")
    stream.start()
    stream.stop(timeout=1.0)
    with pytest.raises(SubscriptionError):
        stream.start()
    with pytest.raises(SubscriptionError):
        stream.subscribe("sz000001")


def test_stateful_stream_fail_closed_if_running_worker_disappears() -> None:
    stream = StatefulQuoteStream(runtime=FakeRuntime())  # type: ignore[arg-type]
    stream._lifecycle.begin_start()
    stream._thread = None
    with pytest.raises(SubscriptionError):
        stream.start()
    assert stream.state is StreamState.FAILED


def test_async_stateful_stream_uses_same_runtime_contract() -> None:
    async def scenario() -> tuple[int, StreamState]:
        runtime = FakeRuntime()
        received = asyncio.Event()
        stream = AsyncStatefulQuoteStream(runtime=runtime)  # type: ignore[arg-type]
        stream.subscribe(
            "sh600519",
            interval=0.01,
            on_quote=lambda symbol, quote: received.set(),
        )
        await stream.start()
        await asyncio.wait_for(received.wait(), timeout=1.0)
        await stream.stop()
        return runtime.calls, stream.state

    calls, state = asyncio.run(scenario())
    assert calls >= 1
    assert state is StreamState.CLOSED


def test_delta_merger_diff_contract() -> None:
    merger = DeltaMerger()
    first = merger.update("600519", {"code": "600519", "price": 1.0, "volume": 10})
    second = merger.update("600519", {"code": "600519", "price": 2.0, "volume": 10})
    assert first["volume"] == 10
    assert second == {"code": "600519", "price": 2.0}


def test_gap_filler_detects_numeric_gap() -> None:
    filler = GapFiller()
    assert filler.observe("x", 1) is True
    assert filler.observe("x", 2) is True
    assert filler.observe("x", 4) is False
    assert filler.gaps("x") == [(2, 4)]


def test_backpressure_queue_drops_oldest() -> None:
    dropped: list[int] = []
    queue = BackpressureQueue(maxsize=2, on_drop=dropped.append)
    queue.put(1)
    queue.put(2)
    queue.put(3)
    assert dropped == [1]
    assert queue.drain() == [2, 3]


def test_reconnect_policy_resets_after_success() -> None:
    policy = ReconnectPolicy(base=0.01, cap=0.02, jitter=False)
    assert policy.next_delay() == 0.01
    assert policy.attempts == 1
    policy.success()
    assert policy.attempts == 0
