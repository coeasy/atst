# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Streaming base classes shared by stateful and planned stream implementations.

These are minimal stubs that provide the common threading/async primitives
(locks, subscriptions dict, stop event, worker thread/task) used by
StatefulQuoteStream / AsyncStatefulQuoteStream. The concrete polling logic
lives in the planned.py scheduler.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

on_quote_t = Callable[[str, dict[str, Any]], None]
on_error_t = Callable[[Exception], None]


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

@dataclass
class Subscription:
    """A single polling subscription with optional bounded queue."""

    symbols: list[str]
    interval: float
    diff_only: bool
    max_queue: int
    on_quote: on_quote_t | None = None
    on_error: on_error_t | None = None
    _queue: Any = field(default=None, repr=False)
    _dropped: int = 0


# ---------------------------------------------------------------------------
# QuoteStream (synchronous base)
# ---------------------------------------------------------------------------

class QuoteStream:
    """Synchronous quote stream base with shared threading primitives."""

    def __init__(self, **kwargs: Any) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, Subscription] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> QuoteStream:
        """Start the polling worker thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the worker to stop and wait for it to finish."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        """Override in subclasses with the polling loop."""
        while not self._stop.is_set():
            self._stop.wait(0.5)

    def _drop(self, item: Any) -> None:
        """Backpressure drop callback (default no-op)."""


# ---------------------------------------------------------------------------
# AsyncQuoteStream (async base)
# ---------------------------------------------------------------------------

class AsyncQuoteStream:
    """Async quote stream base with shared asyncio primitives."""

    def __init__(self, **kwargs: Any) -> None:
        self._subs: dict[str, Subscription] = {}
        self._stop = threading.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> AsyncQuoteStream:
        """Start the polling asyncio task."""
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self) -> None:
        """Signal the task to stop and await completion."""
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            with asyncio.shield(task) as t:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    async def _run(self) -> None:
        """Override in subclasses with the polling loop."""
        while not self._stop.is_set():
            await asyncio.sleep(0.5)
