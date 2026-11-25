# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P0-7 regression: a timed-out async ``stop()`` must fail closed and release the owned runtime.

The sync twin has always set ``_hard_stop`` on a join timeout and closed its owned
runtime.  The async twin used to set only a local ``timed_out`` flag: the lingering
worker could rebuild an orphan pool through ``_get_runtime()`` and ``start()`` could
spawn a second worker, while ``_run.finally`` (the only place that closes a runtime
on the async side) was gated on ``_hard_stop`` and therefore never fired.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from atst.errors import SubscriptionError
from atst.streaming import base as base_module
from atst.streaming.base import AsyncQuoteStream, QuoteStream


class _StuckRuntime:
    """Runtime whose ``quotes()`` blocks until released — simulates a stuck ``to_thread``."""

    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release
        self.calls = 0
        self.closed = False

    def quotes(self, symbols: Any, **kwargs: Any) -> list[Any]:
        del symbols, kwargs
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5.0), "worker was never released"
        return []

    def close(self) -> None:
        self.closed = True


def _own_stuck_runtime(
    monkeypatch: pytest.MonkeyPatch, entered: threading.Event, release: threading.Event
) -> _StuckRuntime:
    """Make a stream *own* a controlled runtime (injected ``runtime=`` would set _owns_runtime=False)."""
    runtime = _StuckRuntime(entered, release)
    kernel = importlib.import_module("atst.runtime.kernel")
    monkeypatch.setattr(kernel, "UnifiedRuntime", lambda **_kwargs: runtime)
    return runtime


@pytest.mark.asyncio
async def test_async_stop_timeout_fails_closed_and_closes_runtime_when_worker_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = threading.Event(), threading.Event()
    runtime = _own_stuck_runtime(monkeypatch, entered, release)

    async def _to_thread_ignoring_cancel(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Stay inside `to_thread` despite ``Task.cancel()`` until the real thread finishes."""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
        return future.result()

    monkeypatch.setattr(
        base_module,
        "asyncio",
        SimpleNamespace(
            to_thread=_to_thread_ignoring_cancel,
            wait=asyncio.wait,
            CancelledError=asyncio.CancelledError,
            create_task=asyncio.create_task,
        ),
    )
    monkeypatch.setattr(base_module, "_STOP_SHIELD_TIMEOUT", 0.05)

    stream = AsyncQuoteStream()
    stream.subscribe("sh600519", interval=0.01)
    await stream.start()
    assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2.0), timeout=3.0)

    await stream.stop()

    assert runtime.calls >= 1
    assert stream._hard_stop is True, "async stop timeout must set the fail-closed flag"
    assert stream._task is not None, "handle must be kept while the worker is still alive"
    assert runtime.closed is False, "stop() must not close the runtime under a live worker"
    with pytest.raises(SubscriptionError):
        stream._get_runtime()
    with pytest.raises(SubscriptionError):
        await stream.start()

    task = stream._task
    release.set()
    await asyncio.wait_for(task, timeout=3.0)
    assert runtime.closed is True, "_run.finally must close the owned runtime when the worker exits"


def test_sync_stop_timeout_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sync twin check: timeout sets _hard_stop, keeps the handle, closes its own runtime."""
    entered, release = threading.Event(), threading.Event()
    runtime = _own_stuck_runtime(monkeypatch, entered, release)

    stream = QuoteStream()
    stream.subscribe("sh600519", interval=0.01)
    stream.start()
    assert entered.wait(timeout=2.0)

    stream.stop(timeout=0.05)

    assert stream._hard_stop is True
    assert stream._thread is not None
    assert runtime.closed is True, "sync stop closes the owned runtime itself on timeout"
    with pytest.raises(SubscriptionError):
        stream._get_runtime()
    with pytest.raises(SubscriptionError):
        stream.start()
    release.set()
    thread = stream._thread
    thread.join(timeout=3.0)
    assert not thread.is_alive()
