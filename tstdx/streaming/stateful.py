# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical Stateful streaming over the same v13 UnifiedRuntime.

TDX quote streaming is client-side polling because the quotation command is
request/response. The important architectural rule is that polling uses the
same QuerySpec -> QueryPlanner -> UnifiedRuntime -> DirectProvider chain as
ordinary quotes. There is no second TdxClient-based streaming business kernel.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain.symbol import normalize_symbol, split_symbol
from ..errors import BackpressureOverflow, GapUnfilledError, SubscriptionError
from ..runtime import UnifiedRuntime
from .engine import BackpressureQueue, DeltaMerger, ReconnectPolicy
from .state import StreamLifecycle, StreamState

__all__ = ["StatefulQuoteStream", "AsyncStatefulQuoteStream", "StreamState"]

on_quote_t = Callable[[str, dict[str, Any]], None]
on_error_t = Callable[[Exception], None]
_DEFAULT_QUEUE = 1024
_MISSING_ALERT_AFTER = 3


@dataclass(slots=True)
class _Subscription:
    symbols: list[str]
    interval: float
    diff_only: bool
    max_queue: int
    on_quote: on_quote_t | None
    on_error: on_error_t | None
    merger: DeltaMerger = field(default_factory=DeltaMerger)
    queue: BackpressureQueue | None = None
    missing: dict[str, int] = field(default_factory=dict)
    dropped: int = 0


def _validate_subscription(
    symbols: str | Sequence[str], *, interval: float, max_queue: int
) -> list[str]:
    raw = [symbols] if isinstance(symbols, str) else list(symbols)
    if not raw:
        raise SubscriptionError(
            "symbols 不能为空", context={"phase": "subscription_validation"}
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
    return [normalize_symbol(item) for item in raw]


def _build_subscription(
    symbols: list[str],
    *,
    interval: float,
    diff_only: bool,
    max_queue: int,
    on_quote: on_quote_t | None,
    on_error: on_error_t | None,
) -> _Subscription:
    sub = _Subscription(
        symbols=symbols,
        interval=float(interval),
        diff_only=bool(diff_only),
        max_queue=int(max_queue),
        on_quote=on_quote,
        on_error=on_error,
        queue=BackpressureQueue(max_queue) if max_queue > 0 else None,
    )
    if sub.queue is not None:
        def _drop(_item: Any) -> None:
            sub.dropped += 1

        sub.queue._on_drop = _drop
    return sub


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        return dict(row.to_dict())
    if hasattr(row, "__dataclass_fields__"):
        from dataclasses import asdict

        return dict(asdict(row))
    raise TypeError(f"unsupported quote row type: {type(row).__name__}")


def _bare_code(symbol: str) -> str:
    return split_symbol(symbol)[1]


def _emit_error(sub: _Subscription, exc: Exception) -> None:
    if sub.on_error is not None:
        with contextlib.suppress(Exception):
            sub.on_error(exc)


def _deliver(sub: _Subscription, symbol: str, payload: dict[str, Any]) -> None:
    bare = _bare_code(symbol)
    effective = dict(sub.merger.update(bare, payload)) if sub.diff_only else payload
    if sub.queue is not None:
        before = sub.dropped
        sub.queue.put((symbol, effective))
        for queued_symbol, queued_payload in sub.queue.drain():
            if sub.on_quote is not None:
                with contextlib.suppress(Exception):
                    sub.on_quote(queued_symbol, queued_payload)
        if sub.dropped > before:
            _emit_error(
                sub,
                BackpressureOverflow(
                    f"背压队列溢出，丢弃 {sub.dropped - before} 个最旧事件",
                    context={"dropped_total": sub.dropped, "max_queue": sub.max_queue},
                ),
            )
    elif sub.on_quote is not None:
        with contextlib.suppress(Exception):
            sub.on_quote(symbol, effective)


def _dispatch_result(subs: list[_Subscription], rows: Any) -> None:
    qmap: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        data = _row_dict(row)
        code = str(data.get("code", ""))
        if code:
            qmap[code] = data

    for sub in subs:
        for symbol in sub.symbols:
            bare = _bare_code(symbol)
            payload = qmap.get(bare)
            if payload is None:
                missing = sub.missing.get(bare, 0) + 1
                sub.missing[bare] = missing
                if missing == _MISSING_ALERT_AFTER:
                    _emit_error(
                        sub,
                        GapUnfilledError(
                            f"stream 连续 {_MISSING_ALERT_AFTER} 轮未收到 {symbol}",
                            context={"symbol": symbol, "missing_rounds": missing},
                        ),
                    )
                continue
            sub.missing[bare] = 0
            _deliver(sub, symbol, payload)


class StatefulQuoteStream:
    """One-shot synchronous Stateful stream using UnifiedRuntime for every poll."""

    def __init__(
        self,
        *,
        runtime: UnifiedRuntime | None = None,
        provider: str = "tdx",
    ) -> None:
        self._runtime = runtime or UnifiedRuntime(default_provider=provider)
        self._owns_runtime = runtime is None
        self._provider = provider
        self._subs: dict[str, _Subscription] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._lifecycle = StreamLifecycle()
        self._subscription_seq = 0
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)

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

    def unsubscribe(self, key: str) -> None:
        with self._lock:
            self._subs.pop(key, None)

    def start(self) -> "StatefulQuoteStream":
        if self.state is StreamState.RUNNING:
            thread = self._thread
            self._lifecycle.assert_running_worker(
                worker_alive=thread is not None and thread.is_alive()
            )
            return self
        self._lifecycle.begin_start()
        self._stop.clear()
        try:
            self._thread = threading.Thread(
                target=self._run,
                name="tstdx-v13-stream",
                daemon=True,
            )
            self._thread.start()
        except BaseException:
            self._lifecycle.fail("worker start failed")
            self._stop.set()
            raise
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        if not self._lifecycle.begin_stop():
            return
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        if thread is not None and thread.is_alive():
            return
        if self._owns_runtime:
            self._runtime.close()
        self._lifecycle.close()

    close = stop

    def __enter__(self) -> "StatefulQuoteStream":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            while not self._stop.is_set():
                with self._lock:
                    subs = list(self._subs.values())
                if not subs:
                    self._stop.wait(0.2)
                    continue
                symbols = list(dict.fromkeys(symbol for sub in subs for symbol in sub.symbols))
                try:
                    result = self._runtime.quotes(
                        symbols,
                        provider=self._provider,
                        currentness="live",
                        max_age=0.0,
                        use_cache=False,
                    )
                    self._reconnect.success()
                    _dispatch_result(subs, result.data)
                except Exception as exc:
                    for sub in subs:
                        _emit_error(sub, exc)
                    self._stop.wait(self._reconnect.next_delay())
                    continue
                self._stop.wait(min(sub.interval for sub in subs))
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                self._lifecycle.fail(
                    f"worker raised {type(unexpected).__name__}"
                    if unexpected is not None
                    else "worker exited unexpectedly"
                )
                self._stop.set()


class AsyncStatefulQuoteStream:
    """Async Stateful stream over the identical UnifiedRuntime semantics."""

    def __init__(
        self,
        *,
        runtime: UnifiedRuntime | None = None,
        provider: str = "tdx",
    ) -> None:
        self._runtime = runtime or UnifiedRuntime(default_provider=provider)
        self._owns_runtime = runtime is None
        self._provider = provider
        self._subs: dict[str, _Subscription] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lifecycle = StreamLifecycle()
        self._subscription_seq = 0
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)

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
        key = f"sub{self._subscription_seq}"
        self._subscription_seq += 1
        self._subs[key] = _build_subscription(
            values,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        return key

    def unsubscribe(self, key: str) -> None:
        self._subs.pop(key, None)

    async def start(self) -> "AsyncStatefulQuoteStream":
        if self.state is StreamState.RUNNING:
            task = self._task
            self._lifecycle.assert_running_worker(
                worker_alive=task is not None and not task.done()
            )
            return self
        self._lifecycle.begin_start()
        self._stop.clear()
        try:
            self._task = asyncio.create_task(self._run(), name="tstdx-v13-async-stream")
        except BaseException:
            self._lifecycle.fail("async worker start failed")
            self._stop.set()
            raise
        return self

    async def stop(self) -> None:
        if not self._lifecycle.begin_stop():
            return
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._owns_runtime:
            await asyncio.to_thread(self._runtime.close)
        self._lifecycle.close()

    close = stop

    async def __aenter__(self) -> "AsyncStatefulQuoteStream":
        return await self.start()

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    async def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            while not self._stop.is_set():
                subs = list(self._subs.values())
                if not subs:
                    await asyncio.sleep(0.2)
                    continue
                symbols = list(dict.fromkeys(symbol for sub in subs for symbol in sub.symbols))
                try:
                    result = await asyncio.to_thread(
                        self._runtime.quotes,
                        symbols,
                        provider=self._provider,
                        currentness="live",
                        max_age=0.0,
                        use_cache=False,
                    )
                    self._reconnect.success()
                    _dispatch_result(subs, result.data)
                except Exception as exc:
                    for sub in subs:
                        _emit_error(sub, exc)
                    await asyncio.sleep(self._reconnect.next_delay())
                    continue
                await asyncio.sleep(min(sub.interval for sub in subs))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                self._lifecycle.fail(
                    f"async worker raised {type(unexpected).__name__}"
                    if unexpected is not None
                    else "async worker exited unexpectedly"
                )
