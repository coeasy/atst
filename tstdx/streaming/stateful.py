# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail-closed streaming facades backed by the explicit lifecycle state machine."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from typing import Any

from ..errors import SubscriptionError
from .base import AsyncQuoteStream, QuoteStream, Subscription, on_error_t, on_quote_t
from .engine import BackpressureQueue
from .state import StreamLifecycle, StreamState

__all__ = ["StatefulQuoteStream", "AsyncStatefulQuoteStream", "StreamState"]

_DEFAULT_QUEUE = 1024


def _validate_subscription(
    symbols: str | Sequence[str], *, interval: float, max_queue: int
) -> list[str]:
    values = [symbols] if isinstance(symbols, str) else list(symbols)
    if not values:
        raise SubscriptionError(
            "symbols 不能为空",
            context={"phase": "subscription_validation"},
        )
    if interval <= 0:
        raise SubscriptionError(
            "interval 必须大于 0",
            context={"interval": interval, "phase": "subscription_validation"},
        )
    if max_queue < 0:
        raise SubscriptionError(
            "max_queue 不能为负数",
            context={"max_queue": max_queue, "phase": "subscription_validation"},
        )
    return values


def _build_subscription(
    symbols: list[str],
    *,
    interval: float,
    diff_only: bool,
    max_queue: int,
    on_quote: on_quote_t | None,
    on_error: on_error_t | None,
) -> Subscription:
    queue = BackpressureQueue(max_queue) if max_queue > 0 else None
    sub = Subscription(
        symbols=symbols,
        interval=float(interval),
        diff_only=bool(diff_only),
        max_queue=int(max_queue),
        on_quote=on_quote,
        on_error=on_error,
        _queue=queue,
    )
    if queue is not None:

        def _on_drop(_item: Any, _sub: Subscription = sub) -> None:
            _sub._dropped += 1

        queue._on_drop = _on_drop
    return sub


class StatefulQuoteStream(QuoteStream):
    """Canonical synchronous stream with explicit one-shot lifecycle semantics."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lifecycle = StreamLifecycle()
        self._subscription_seq = 0

    @property
    def state(self) -> StreamState:
        return self._lifecycle.state

    @property
    def failure_reason(self) -> str | None:
        return self._lifecycle.failure_reason

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _DEFAULT_QUEUE,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = _validate_subscription(symbols, interval=interval, max_queue=max_queue)
        self._lifecycle.require_subscribable()
        sub = _build_subscription(
            values,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        with self._lock:
            self._lifecycle.require_subscribable()
            key = f"sub{self._subscription_seq}"
            self._subscription_seq += 1
            self._subs[key] = sub
        return key

    def start(self) -> StatefulQuoteStream:
        if self.state is StreamState.RUNNING:
            thread = self._thread
            self._lifecycle.assert_running_worker(
                worker_alive=thread is not None and thread.is_alive()
            )
            return self
        self._lifecycle.begin_start()
        try:
            super().start()
        except BaseException:
            self._stop.set()
            self._lifecycle.fail("worker start failed")
            raise
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        if not self._lifecycle.begin_stop():
            return
        super().stop(timeout=timeout)
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._lifecycle.close()

    close = stop

    def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            super()._run()
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                reason = (
                    f"worker raised {type(unexpected).__name__}"
                    if unexpected is not None
                    else "worker exited unexpectedly"
                )
                self._lifecycle.fail(reason)
                self._stop.set()


class AsyncStatefulQuoteStream(AsyncQuoteStream):
    """Canonical asyncio stream mirroring the synchronous lifecycle contract."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lifecycle = StreamLifecycle()
        self._subscription_seq = 0

    @property
    def state(self) -> StreamState:
        return self._lifecycle.state

    @property
    def failure_reason(self) -> str | None:
        return self._lifecycle.failure_reason

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _DEFAULT_QUEUE,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = _validate_subscription(symbols, interval=interval, max_queue=max_queue)
        self._lifecycle.require_subscribable()
        sub = _build_subscription(
            values,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        key = f"sub{self._subscription_seq}"
        self._subscription_seq += 1
        self._subs[key] = sub
        return key

    async def start(self) -> AsyncStatefulQuoteStream:
        if self.state is StreamState.RUNNING:
            task = self._task
            self._lifecycle.assert_running_worker(worker_alive=task is not None and not task.done())
            return self
        self._lifecycle.begin_start()
        try:
            await super().start()
        except BaseException:
            self._stop.set()
            self._lifecycle.fail("async worker start failed")
            raise
        return self

    async def stop(self) -> None:
        if not self._lifecycle.begin_stop():
            return
        await super().stop()
        self._lifecycle.close()

    close = stop

    async def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            await super()._run()
        except asyncio.CancelledError:
            if self.state is StreamState.RUNNING:
                unexpected = asyncio.CancelledError()
            raise
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                reason = (
                    f"worker raised {type(unexpected).__name__}"
                    if unexpected is not None
                    else "async worker exited unexpectedly"
                )
                self._lifecycle.fail(reason)
                self._stop.set()
            with contextlib.suppress(Exception):
                if (
                    self._task is not None
                    and self._task.done()
                    and self.state is StreamState.STOPPING
                ):
                    self._lifecycle.close()
