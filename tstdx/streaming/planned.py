# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Due-aware streaming scheduler over the planned Provider service.

One fast subscription does not force slower subscriptions to poll at the fastest
interval. Only due subscriptions are unioned/deduplicated for one upstream
fetch. Callback dispatch runs behind bounded per-subscription queues.

Polling Providers do not expose a trustworthy transport sequence, so this module
does not invent one. Instead it tracks auditable per-symbol watermarks: last
local observation, Provider timestamp when present, consecutive missing rounds,
open-gap state, recovery count, and reconnect/resubscribe epochs.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain.models import Quote
from ..domain.symbol import normalize_symbol, split_symbol
from ..errors import (
    BackpressureOverflow,
    GapUnfilledError,
    RetryAdvice,
    SubscriptionError,
    TdxError,
)
from ..observability.planned import record_stream_gap, record_stream_reconnect
from ..planned_service import UnifiedMarketDataService
from ..providers import PROVIDERS, resolve_provider
from .engine import BackpressureQueue, DeltaMerger, ReconnectPolicy

__all__ = [
    "PlannedQuoteStream",
    "StreamSubscription",
    "StreamStats",
    "StreamWatermark",
]

_LOG = logging.getLogger(__name__)
_MISSING_ALERT_AFTER = 3
_INPUT_ADVICE = RetryAdvice(
    retryable=False,
    switch_host=False,
    note="订阅参数错误不可通过重试、重连或换 host 恢复",
)

QuoteCallback = Callable[[str, dict[str, Any]], None]
ErrorCallback = Callable[[Exception], None]


def _input_error(
    message: str,
    *,
    context: dict[str, Any],
    cause: Exception | None = None,
) -> SubscriptionError:
    return SubscriptionError(
        message,
        context=context,
        cause=cause,
        advice=_INPUT_ADVICE,
    )


def _normalize_subscription_symbol(symbol: str) -> str:
    try:
        return normalize_symbol(symbol)
    except Exception as exc:
        raise _input_error(
            f"无法解析订阅 symbol {symbol!r}",
            context={"symbol": symbol, "phase": "subscription_validation"},
            cause=exc,
        ) from exc


def _bare_code(symbol: str) -> str:
    try:
        return split_symbol(symbol)[1]
    except Exception as exc:
        raise _input_error(
            f"无法解析订阅 symbol {symbol!r}",
            context={"symbol": symbol, "phase": "subscription_validation"},
            cause=exc,
        ) from exc


def _quote_key(quote: Quote) -> str:
    code = str(quote.code or "")
    try:
        return split_symbol(code)[1]
    except Exception:
        return code[-6:]


@dataclass(slots=True)
class StreamStats:
    polls: int = 0
    requested_symbols: int = 0
    delivered_events: int = 0
    dropped_events: int = 0
    provider_errors: int = 0
    gaps_detected: int = 0
    gaps_recovered: int = 0
    reconnects: int = 0
    resubscriptions: int = 0


@dataclass(slots=True)
class StreamWatermark:
    """Auditable polling watermark without fabricated sequence numbers."""

    last_seen_monotonic: float | None = None
    provider_timestamp: str | None = None
    missing_rounds: int = 0
    gap_open: bool = False
    recoveries: int = 0
    reconnect_epoch: int = 0
    resubscriptions: int = 0
    last_reconnect_monotonic: float | None = None


@dataclass(slots=True)
class StreamSubscription:
    id: str
    symbols: tuple[str, ...]
    interval: float
    next_due: float
    diff_only: bool
    max_queue: int
    on_quote: QuoteCallback | None
    on_error: ErrorCallback | None
    merger: DeltaMerger = field(default_factory=DeltaMerger, repr=False)
    queue: BackpressureQueue | None = field(default=None, repr=False)
    missing: dict[str, int] = field(default_factory=dict, repr=False)
    watermarks: dict[str, StreamWatermark] = field(default_factory=dict, repr=False)
    resubscribe_epoch: int = 0
    dropped: int = 0
    reported_dropped: int = 0


class PlannedQuoteStream:
    """Single-Provider, due-aware realtime quote scheduler."""

    def __init__(
        self,
        *,
        provider: str = "tdx",
        service: UnifiedMarketDataService | None = None,
        callback_idle_wait: float = 0.05,
        reconnect: ReconnectPolicy | None = None,
        **service_kwargs: Any,
    ) -> None:
        pid = resolve_provider(provider=provider)
        PROVIDERS.require(pid, "quotes")
        if callback_idle_wait <= 0:
            raise ValueError("callback_idle_wait must be > 0")
        self.provider = pid
        self.service = service if service is not None else UnifiedMarketDataService(**service_kwargs)
        self._owns_service = service is None
        self.callback_idle_wait = float(callback_idle_wait)
        self.reconnect = reconnect or ReconnectPolicy(base=0.5, cap=15.0)
        self.stats = StreamStats()

        self._subs: dict[str, StreamSubscription] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._dispatch_wakeup = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._reconnect_pending = False
        self._reconnect_epoch = 0

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: QuoteCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> str:
        if interval <= 0:
            raise _input_error(
                "interval 必须大于 0",
                context={"interval": interval, "phase": "subscription_validation"},
            )
        if max_queue <= 0:
            raise _input_error(
                "PlannedQuoteStream.max_queue 必须大于 0；回调必须与 poll 解耦",
                context={"max_queue": max_queue, "phase": "subscription_validation"},
            )
        raw = [symbols] if isinstance(symbols, str) else list(symbols)
        if not raw:
            raise _input_error(
                "symbols 不能为空",
                context={"phase": "subscription_validation"},
            )
        normalized = tuple(_normalize_subscription_symbol(symbol) for symbol in raw)
        watermarks = {_bare_code(symbol): StreamWatermark() for symbol in normalized}
        sub_id = uuid.uuid4().hex[:12]
        sub = StreamSubscription(
            id=sub_id,
            symbols=normalized,
            interval=float(interval),
            next_due=time.monotonic(),
            diff_only=bool(diff_only),
            max_queue=int(max_queue),
            on_quote=on_quote,
            on_error=on_error,
            watermarks=watermarks,
        )

        def on_drop(_item: Any, current: StreamSubscription = sub) -> None:
            current.dropped += 1
            self.stats.dropped_events += 1

        sub.queue = BackpressureQueue(max_queue, on_drop=on_drop)
        with self._lock:
            self._subs[sub_id] = sub
        self._dispatch_wakeup.set()
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        with self._lock:
            removed = self._subs.pop(subscription_id, None)
        return removed is not None

    def watermark(self, subscription_id: str, symbol: str) -> StreamWatermark:
        """Return a consistent snapshot for one actually subscribed symbol."""
        normalized = _normalize_subscription_symbol(symbol)
        key = _bare_code(normalized)
        with self._lock:
            sub = self._subs.get(subscription_id)
            if sub is None:
                raise _input_error(
                    "未知 subscription_id",
                    context={
                        "subscription_id": subscription_id,
                        "phase": "subscription_lookup",
                    },
                )
            if normalized not in sub.symbols:
                raise _input_error(
                    "symbol 不属于该 subscription",
                    context={
                        "subscription_id": subscription_id,
                        "symbol": normalized,
                        "phase": "subscription_lookup",
                    },
                )
            value = sub.watermarks[key]
            return replace(value)

    def start(self) -> "PlannedQuoteStream":
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return self
        self._stop.clear()
        with self._lock:
            self._reconnect_pending = False
        self.reconnect.success()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            name=f"tstdx-planned-poll-{self.provider}",
            daemon=True,
        )
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop,
            name=f"tstdx-planned-dispatch-{self.provider}",
            daemon=True,
        )
        self._poll_thread.start()
        self._dispatch_thread.start()
        return self

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop.set()
        self._dispatch_wakeup.set()
        poll = self._poll_thread
        dispatch = self._dispatch_thread
        if poll is not None:
            poll.join(timeout=timeout)
        if dispatch is not None:
            dispatch.join(timeout=timeout)
        if poll is not None and poll.is_alive():
            _LOG.warning("planned stream poll thread did not stop within timeout")
            return
        self._poll_thread = None
        self._dispatch_thread = None if dispatch is None or not dispatch.is_alive() else dispatch
        if self._owns_service:
            with contextlib.suppress(Exception):
                self.service.close()

    close = stop

    def __enter__(self) -> "PlannedQuoteStream":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _subscriptions(self) -> list[StreamSubscription]:
        with self._lock:
            return list(self._subs.values())

    @staticmethod
    def _advance_due(sub: StreamSubscription, now: float) -> None:
        while sub.next_due <= now:
            sub.next_due += sub.interval

    @staticmethod
    def _union_symbols(subs: list[StreamSubscription]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for sub in subs:
            for symbol in sub.symbols:
                if symbol not in seen:
                    seen.add(symbol)
                    ordered.append(symbol)
        return ordered

    def _mark_reconnect_pending(self) -> None:
        detected = False
        with self._lock:
            if not self._reconnect_pending:
                self._reconnect_pending = True
                detected = True
        if detected:
            record_stream_reconnect(provider=self.provider, kind="detected")

    def _mark_reconnected(self, now: float | None = None) -> bool:
        """Advance one reconnect epoch after the first successful post-failure poll.

        Polling has no remote subscribe frame. "resubscribe" therefore means the
        active local subscriptions are moved to the new continuity epoch and are
        scheduled for a prompt refresh. Gap state is preserved until each symbol
        is actually observed again.
        """
        recovered_at = time.monotonic() if now is None else float(now)
        with self._lock:
            if not self._reconnect_pending:
                return False
            self._reconnect_pending = False
            self._reconnect_epoch += 1
            epoch = self._reconnect_epoch
            self.stats.reconnects += 1
            subscriptions = list(self._subs.values())
            for sub in subscriptions:
                sub.resubscribe_epoch = epoch
                self.stats.resubscriptions += 1
                if sub.next_due > recovered_at:
                    sub.next_due = recovered_at
                for watermark in sub.watermarks.values():
                    watermark.reconnect_epoch = epoch
                    watermark.resubscriptions += 1
                    watermark.last_reconnect_monotonic = recovered_at

        record_stream_reconnect(provider=self.provider, kind="recovered")
        if subscriptions:
            record_stream_reconnect(provider=self.provider, kind="resubscribed")
        return True

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            subs = self._subscriptions()
            if not subs:
                self._stop.wait(0.2)
                continue

            now = time.monotonic()
            due = [sub for sub in subs if sub.next_due <= now]
            if not due:
                next_due = min(sub.next_due for sub in subs)
                self._stop.wait(min(max(next_due - now, 0.001), 0.5))
                continue

            symbols = self._union_symbols(due)
            try:
                result = self.service.quotes(symbols, provider=self.provider)
            except TdxError as exc:
                self.stats.provider_errors += 1
                self._mark_reconnect_pending()
                for sub in due:
                    self._enqueue_error(sub, exc)
                    self._advance_due(sub, now)
                self._stop.wait(self.reconnect.next_delay())
                continue
            except Exception as exc:
                self.stats.provider_errors += 1
                self._mark_reconnect_pending()
                _LOG.exception("planned stream unexpected provider error")
                wrapped = SubscriptionError(
                    "planned stream 上游调用失败",
                    context={"provider": self.provider, "fallback": False},
                    cause=exc,
                )
                for sub in due:
                    self._enqueue_error(sub, wrapped)
                    self._advance_due(sub, now)
                self._stop.wait(self.reconnect.next_delay())
                continue

            if not isinstance(result, list) or not all(isinstance(row, Quote) for row in result):
                contract_error = SubscriptionError(
                    "planned stream service 必须返回 list[Quote]",
                    context={
                        "provider": self.provider,
                        "phase": "stream_contract",
                        "fallback": False,
                    },
                )
                for sub in due:
                    self._enqueue_error(sub, contract_error)
                    self._advance_due(sub, now)
                continue

            rows = result
            self._mark_reconnected()
            self.reconnect.success()
            self.stats.polls += 1
            self.stats.requested_symbols += len(symbols)

            qmap = {_quote_key(quote): quote for quote in rows}
            for sub in due:
                self._deliver_subscription(sub, qmap)
                self._advance_due(sub, now)

    def _mark_missing(self, sub: StreamSubscription, key: str, symbol: str) -> None:
        emit_gap = False
        missing_rounds = 0
        with self._lock:
            watermark = sub.watermarks.setdefault(key, StreamWatermark())
            watermark.missing_rounds += 1
            missing_rounds = watermark.missing_rounds
            sub.missing[key] = missing_rounds
            if missing_rounds == _MISSING_ALERT_AFTER:
                watermark.gap_open = True
                self.stats.gaps_detected += 1
                emit_gap = True
        if emit_gap:
            record_stream_gap(provider=self.provider, kind="detected")
            self._enqueue_error(
                sub,
                GapUnfilledError(
                    f"订阅标的 {symbol} 连续 {_MISSING_ALERT_AFTER} 轮未返回",
                    context={
                        "provider": self.provider,
                        "symbol": symbol,
                        "missing_rounds": missing_rounds,
                        "fallback": False,
                    },
                ),
            )

    def _mark_seen(self, sub: StreamSubscription, key: str, quote: Quote) -> None:
        recovered = False
        with self._lock:
            watermark = sub.watermarks.setdefault(key, StreamWatermark())
            recovered = watermark.gap_open
            watermark.last_seen_monotonic = time.monotonic()
            watermark.provider_timestamp = str(quote.datetime) if quote.datetime else None
            watermark.missing_rounds = 0
            watermark.gap_open = False
            sub.missing[key] = 0
            if recovered:
                watermark.recoveries += 1
                self.stats.gaps_recovered += 1
        if recovered:
            record_stream_gap(provider=self.provider, kind="recovered")

    def _deliver_subscription(self, sub: StreamSubscription, qmap: dict[str, Quote]) -> None:
        for symbol in sub.symbols:
            try:
                key = _bare_code(symbol)
            except SubscriptionError as exc:
                self._enqueue_error(sub, exc)
                continue
            quote = qmap.get(key)
            if quote is None:
                self._mark_missing(sub, key, symbol)
                continue

            self._mark_seen(sub, key, quote)
            payload = quote.to_dict()
            if sub.diff_only:
                payload = dict(sub.merger.update(key, payload))
                if set(payload) <= {"code"}:
                    continue
            self._enqueue(sub, ("quote", symbol, payload))

    def _enqueue_error(self, sub: StreamSubscription, exc: Exception) -> None:
        self._enqueue(sub, ("error", "", exc))

    def _enqueue(self, sub: StreamSubscription, item: tuple[str, str, Any]) -> None:
        queue = sub.queue
        if queue is None:
            return
        queue.put(item)
        self._dispatch_wakeup.set()

    def _has_pending(self) -> bool:
        return any(
            (sub.queue is not None and sub.queue.qsize() > 0)
            or sub.dropped > sub.reported_dropped
            for sub in self._subscriptions()
        )

    def _dispatch_overflow(self, sub: StreamSubscription) -> bool:
        if sub.dropped <= sub.reported_dropped:
            return False
        dropped_since_last = sub.dropped - sub.reported_dropped
        sub.reported_dropped = sub.dropped
        if sub.on_error is None:
            return False
        try:
            sub.on_error(
                BackpressureOverflow(
                    f"订阅回调队列溢出，新增丢弃 {dropped_since_last} 个最旧事件",
                    context={
                        "subscription_id": sub.id,
                        "dropped": dropped_since_last,
                        "dropped_total": sub.dropped,
                        "max_queue": sub.max_queue,
                    },
                )
            )
        except Exception:
            _LOG.exception("planned stream overflow callback failed")
        return True

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set() or self._has_pending():
            delivered = False
            for sub in self._subscriptions():
                queue = sub.queue
                if queue is not None:
                    for kind, symbol, payload in queue.drain():
                        delivered = True
                        if kind == "quote" and sub.on_quote is not None:
                            try:
                                sub.on_quote(symbol, payload)
                                self.stats.delivered_events += 1
                            except Exception:
                                _LOG.exception("planned stream quote callback failed")
                        elif kind == "error" and sub.on_error is not None:
                            try:
                                sub.on_error(payload)
                            except Exception:
                                _LOG.exception("planned stream error callback failed")
                delivered = self._dispatch_overflow(sub) or delivered
            if not delivered:
                self._dispatch_wakeup.wait(self.callback_idle_wait)
                self._dispatch_wakeup.clear()
