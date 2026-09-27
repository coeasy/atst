# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Streaming base classes shared by stateful and planned stream implementations.

Both bases own the polling kernel required by the canonical contract in
:mod:`tstdx.streaming`: a subscription is polled through one
:class:`~tstdx.runtime.kernel.UnifiedRuntime` (so streaming shares the exact
Provider / Planner / Result semantics of ordinary queries), responses are
delta-merged when ``diff_only`` is set, bounded queues provide backpressure, and
failures back off through :class:`~tstdx.streaming.engine.ReconnectPolicy`.

The concrete :class:`StatefulQuoteStream` / :class:`AsyncStatefulQuoteStream`
subclasses add the explicit one-shot lifecycle state machine on top; the
``planned`` scheduler is an alternative scheduler for due-aware polling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..domain.symbol import split_symbol as _symbol_split
from ..errors import (
    BackpressureOverflow,
    GapUnfilledError,
    SubscriptionError,
    TdxError,
)
from .engine import BackpressureQueue, DeltaMerger, ReconnectPolicy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

on_quote_t = Callable[[str, dict[str, Any]], None]
on_error_t = Callable[[Exception], None]

#: Consecutive rounds a subscribed symbol may be absent from the response before
#: a :class:`~tstdx.errors.GapUnfilledError` observability signal is emitted.
_MISSING_ALERT_AFTER = 3


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


def validate_subscription(
    symbols: str | Sequence[str], *, interval: float, max_queue: int
) -> list[str]:
    """One subscription contract for every base and facade.

    ``interval`` must be positive because the polling kernel sleeps on it
    (:meth:`QuoteStream._poll_once`, :meth:`AsyncQuoteStream._poll_once`): a zero
    interval turns the worker thread into a tight request loop against the host.
    """
    values = [symbols] if isinstance(symbols, str) else list(symbols)
    if not values:
        raise SubscriptionError(
            "symbols 不能为空",
            context={"phase": "subscription_validation"},
        )
    if interval <= 0:
        raise SubscriptionError(
            "interval 必须大于 0（轮询内核按它 sleep，0 会退化成忙等）",
            context={"interval": interval, "phase": "subscription_validation"},
        )
    if max_queue < 0:
        raise SubscriptionError(
            "max_queue 不能为负数",
            context={"max_queue": max_queue, "phase": "subscription_validation"},
        )
    return values


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
    #: Per-bare-code consecutive-missing counter (gap observability).
    _missing: dict[str, int] = field(default_factory=dict, repr=False)
    #: Per-bare-code last-snapshot merger, only consulted when ``diff_only``.
    _merger: DeltaMerger = field(default_factory=DeltaMerger, repr=False)

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise SubscriptionError(
                "interval 必须大于 0（轮询内核按它 sleep，0 会退化成忙等）",
                context={"interval": self.interval, "phase": "subscription_validation"},
            )


# ---------------------------------------------------------------------------
# Row/payload helpers
# ---------------------------------------------------------------------------


def _bare_code(symbol: str) -> str:
    """Return the bare 6-digit code used as the 0x0530 echo key."""
    return _symbol_split(symbol)[1]


def _as_row(item: Any) -> dict[str, Any] | None:
    """Normalize one runtime result row into a mapping.

    The runtime returns canonical :class:`~tstdx.domain.models.Quote` models,
    but duck-typed test doubles (and dict-format callers) may hand back plain
    mappings. Both shapes are accepted; ``None`` means "not a quote row".
    """
    if isinstance(item, Mapping):
        return dict(item)
    for attr in ("to_dict", "model_dump"):
        converter = getattr(item, attr, None)
        if callable(converter):
            with contextlib.suppress(Exception):
                produced = converter()
                if isinstance(produced, Mapping):
                    return dict(produced)
    code = getattr(item, "code", None)
    if code is None:
        return None
    return {"code": code, "price": getattr(item, "price", None)}


def _quote_rows(result: Any) -> list[dict[str, Any]]:
    """Extract quote rows from a runtime result (``QueryResult`` or a list)."""
    payload = getattr(result, "data", result)
    if payload is None:
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        row = _as_row(item)
        if row is not None:
            rows.append(row)
    return rows


def _resolve_payload(
    sub: Subscription,
    sym: str,
    qmap: dict[str, dict[str, Any]],
    warned_bad_symbols: set[str],
) -> dict[str, Any] | None:
    """Per-symbol "parse -> missing detection -> merge". ``None`` = no payload this round.

    Error surface (shared by the sync and async streams):

    * unparseable symbol -> :class:`SubscriptionError` (once per symbol);
    * symbol absent for ``_MISSING_ALERT_AFTER`` consecutive rounds ->
      :class:`GapUnfilledError` (observability only; the caller is still
      responsible for backfilling history through ``bars``).
    """
    try:
        bare = _bare_code(sym)
    except TdxError as exc:
        if sym not in warned_bad_symbols:
            warned_bad_symbols.add(sym)
            logger.warning("流式订阅符号无法解析，已跳过: %r (%s)", sym, exc)
            if sub.on_error is not None:
                with contextlib.suppress(Exception):
                    sub.on_error(
                        SubscriptionError(
                            f"订阅符号无法解析: {sym!r}",
                            context={"symbol": sym, "cause": str(exc)},
                            cause=exc,
                        )
                    )
        return None
    current = qmap.get(bare)
    if current is None:
        sub._missing[bare] = sub._missing.get(bare, 0) + 1
        if sub._missing[bare] == _MISSING_ALERT_AFTER and sub.on_error is not None:
            with contextlib.suppress(Exception):
                sub.on_error(
                    GapUnfilledError(
                        f"标的连续 {_MISSING_ALERT_AFTER} 轮未见于响应: {sym!r}",
                        context={"symbol": sym, "missing_rounds": sub._missing[bare]},
                    )
                )
        return None
    sub._missing.pop(bare, None)
    return dict(sub._merger.update(bare, current)) if sub.diff_only else current


def _dispatch_round(
    subs: Sequence[Subscription],
    qmap: dict[str, dict[str, Any]],
    warned_bad_symbols: set[str],
) -> None:
    """Fan one fetched round out to every subscription (queue or direct callback)."""
    for sub in subs:
        dropped_before = sub._dropped
        if sub._queue is not None:
            for sym in sub.symbols:
                payload = _resolve_payload(sub, sym, qmap, warned_bad_symbols)
                if payload is not None:
                    sub._queue.put((sym, payload))
            for queued_sym, queued_quote in sub._queue.drain():
                if sub.on_quote is not None:
                    with contextlib.suppress(Exception):
                        sub.on_quote(queued_sym, queued_quote)
            if sub._dropped > dropped_before and sub.on_error is not None:
                with contextlib.suppress(Exception):
                    sub.on_error(
                        BackpressureOverflow(
                            f"背压队列溢出，丢弃 {sub._dropped - dropped_before} 个最旧事件",
                            context={
                                "dropped_total": sub._dropped,
                                "max_queue": sub.max_queue,
                            },
                        )
                    )
        else:
            for sym in sub.symbols:
                payload = _resolve_payload(sub, sym, qmap, warned_bad_symbols)
                if payload is not None and sub.on_quote is not None:
                    with contextlib.suppress(Exception):
                        sub.on_quote(sym, payload)


# ---------------------------------------------------------------------------
# QuoteStream (synchronous base)
# ---------------------------------------------------------------------------


class QuoteStream:
    """Synchronous quote stream that polls one Provider through ``UnifiedRuntime``.

    Examples
    --------
    >>> stream = QuoteStream()
    >>> stream.subscribe("sh600519", on_quote=lambda code, quote: print(code, quote))
    >>> stream.start()
    >>> time.sleep(5)
    >>> stream.stop()
    """

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
        runtime: Any | None = None,
        provider: str | None = None,
    ) -> None:
        self._hosts = hosts
        self._timeout = timeout
        self._runtime = runtime
        #: An injected runtime is owned by the caller; only a lazily built one
        #: may be closed by ``stop()``.
        self._owns_runtime = runtime is None
        self._provider = provider
        self._lock = threading.RLock()
        self._subs: dict[str, Subscription] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)
        self._warned_bad_symbols: set[str] = set()

    # -- runtime ---------------------------------------------------------- #
    def _get_runtime(self) -> Any:
        if self._runtime is None:
            from ..runtime.kernel import UnifiedRuntime

            self._runtime = UnifiedRuntime(
                default_provider=self._provider or "tdx",
                hosts=list(self._hosts) if self._hosts else None,
                timeout=self._timeout,
            )
        return self._runtime

    # -- subscription management ------------------------------------------ #
    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = validate_subscription(symbols, interval=interval, max_queue=max_queue)
        queue = BackpressureQueue(max_queue) if max_queue > 0 else None
        with self._lock:  # symmetric with ``unsubscribe``
            key = f"sub{len(self._subs)}"
            sub = Subscription(
                symbols=values,
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
            self._subs[key] = sub
        return key

    def unsubscribe(self, key: str) -> None:
        """Remove a subscription by id.

        Symmetric with ``subscribe`` (same lock), and idempotent: removing an
        unknown or already-removed key is a silent no-op so a UI teardown path
        can unsubscribe defensively without racing the worker thread.
        """
        with self._lock:
            self._subs.pop(key, None)

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> QuoteStream:
        """Start the polling worker thread."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tstdx-stream", daemon=True)
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the worker to stop, wait for it, then release an owned runtime."""
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                # Keep the thread reference on a join timeout: clearing it would
                # let a later ``start()`` believe no worker is running and spawn
                # a second one sharing ``_subs``/``_stop``.  But the owned runtime
                # release must NOT be skipped with it —— 那是这条流的连接池，跳过一次
                # 超时停机就漏一整个池（第 26 轮 F-91）。关它反而替老线程收尾：在飞的
                # poll 立刻抛错，被 ``_run`` 的异常分支吃掉后 ``_stop`` 已置位，线程自退。
                logger.warning("QuoteStream 轮询线程未在 %ss 内退出，等待其自行收尾", timeout)
                self._close_owned_runtime()
                return
        self._thread = None
        self._close_owned_runtime()

    def _close_owned_runtime(self) -> None:
        if not self._owns_runtime:
            return
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime.close()

    def __enter__(self) -> QuoteStream:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- polling kernel ---------------------------------------------------- #
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 - never let the worker die silently
                logger.exception("QuoteStream 轮询循环未预期异常（线程继续运行）")
                self._dispatch_error(exc)
                # Back off so a hard failure does not spin; ``Event.wait`` keeps
                # ``stop()`` responsive while we sleep.
                self._stop.wait(self._reconnect.next_delay())

    def _poll_once(self) -> None:
        """One "fetch -> fan out -> backpressure -> callback" round."""
        with self._lock:
            subs = list(self._subs.values())
        if not subs:
            self._stop.wait(0.2)
            return
        all_syms = [sym for sub in subs for sym in sub.symbols]
        try:
            result = self._get_runtime().quotes(
                all_syms,
                provider=self._provider,
                currentness="live",
            )
            self._reconnect.success()
        except TdxError as exc:
            self._dispatch_error(exc)
            # Exponential backoff with jitter and cap (ReconnectPolicy).
            self._stop.wait(self._reconnect.next_delay())
            return

        # The 0x0530 echo carries the bare 6-digit code, so key by bare code.
        qmap = {str(row.get("code", "")): row for row in _quote_rows(result)}
        _dispatch_round(subs, qmap, self._warned_bad_symbols)
        # Sleep for the fastest subscription only.
        self._stop.wait(min((sub.interval for sub in subs), default=1.0))

    def _dispatch_error(self, exc: Exception) -> None:
        with self._lock:
            subs = list(self._subs.values())
        for sub in subs:
            if sub.on_error is not None:
                with contextlib.suppress(Exception):
                    sub.on_error(exc)


# ---------------------------------------------------------------------------
# AsyncQuoteStream (async base)
# ---------------------------------------------------------------------------


class AsyncQuoteStream:
    """Async quote stream mirroring :class:`QuoteStream` on one event loop."""

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
        runtime: Any | None = None,
        provider: str | None = None,
    ) -> None:
        self._hosts = hosts
        self._timeout = timeout
        self._runtime = runtime
        self._owns_runtime = runtime is None
        self._provider = provider
        self._subs: dict[str, Subscription] = {}
        self._stop = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)
        self._warned_bad_symbols: set[str] = set()

    def _get_runtime(self) -> Any:
        if self._runtime is None:
            from ..runtime.kernel import UnifiedRuntime

            self._runtime = UnifiedRuntime(
                default_provider=self._provider or "tdx",
                hosts=list(self._hosts) if self._hosts else None,
                timeout=self._timeout,
            )
        return self._runtime

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = validate_subscription(symbols, interval=interval, max_queue=max_queue)
        queue = BackpressureQueue(max_queue) if max_queue > 0 else None
        key = f"sub{len(self._subs)}"
        sub = Subscription(
            symbols=values,
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
        self._subs[key] = sub
        return key

    def unsubscribe(self, key: str) -> None:
        """Remove a subscription by id (async mirror of the sync base).

        Kept synchronous on purpose: ``subscribe`` is also synchronous, and
        there is no ``await`` point inside the single event loop between a
        subscribe/unsubscribe and the worker's snapshot read.
        """
        self._subs.pop(key, None)

    async def start(self) -> AsyncQuoteStream:
        """Start the polling asyncio task."""
        if self._task is not None and not self._task.done():
            return self
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self) -> None:
        """Signal the task to stop, await it, then release an owned runtime.

        收尾与调用方取消解耦，口径与 ``transport/async_.py`` 的
        :func:`~tstdx.transport.async_._await_cleanup_before_cancellation` 逐条相同：
        被 shield 的 worker 排空之前不放手（``to_thread`` 取数不可取消，半途丢下它就是
        让它在池已关的情况下继续跑），排空之后把取消**原样抛回**。

        这一格原先写的是 ``with contextlib.suppress(asyncio.CancelledError)``，那不只是
        少抛一次取消：吞掉之后 ``_task = None`` 会在 worker 仍活着时清空句柄、并顺手关掉
        它正在用的那份 owned runtime，而 ``_stop`` 已置位又让下一次 ``start()`` 起第二条
        worker 共享同一份 ``_subs``——与同步侧第 26 轮 F-91/F-92 要挡住的是同一件事。
        现在句柄只在 worker 确实跑完之后才清空，取消也不再被吞。
        """
        self._stop.set()
        task = self._task
        cancelled = False
        while task is not None and not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                continue
        #: 句柄到这里才清空：上面的循环只在 worker 跑完之后才退出（取消不打断这段），
        #: 所以"清句柄"与"worker 已死"是同一件事。
        self._task = None
        self._close_owned_runtime()
        if cancelled:
            raise asyncio.CancelledError

    def _close_owned_runtime(self) -> None:
        if not self._owns_runtime:
            return
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime.close()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let the worker die silently
                logger.exception("AsyncQuoteStream 轮询循环未预期异常（任务继续运行）")
                self._dispatch_error(exc)
                await asyncio.sleep(self._reconnect.next_delay())

    async def _poll_once(self) -> None:
        subs = list(self._subs.values())
        if not subs:
            await asyncio.sleep(0.2)
            return
        all_syms = [sym for sub in subs for sym in sub.symbols]
        try:
            result = await asyncio.to_thread(
                self._get_runtime().quotes,
                all_syms,
                provider=self._provider,
                currentness="live",
            )
            self._reconnect.success()
        except TdxError as exc:
            self._dispatch_error(exc)
            await asyncio.sleep(self._reconnect.next_delay())
            return

        qmap = {str(row.get("code", "")): row for row in _quote_rows(result)}
        _dispatch_round(subs, qmap, self._warned_bad_symbols)
        await asyncio.sleep(min((sub.interval for sub in subs), default=1.0))

    def _dispatch_error(self, exc: Exception) -> None:
        for sub in list(self._subs.values()):
            if sub.on_error is not None:
                with contextlib.suppress(Exception):
                    sub.on_error(exc)
