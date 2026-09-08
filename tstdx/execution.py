# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Execution primitives shared by sync/async market-data entrypoints.

This module deliberately contains no Provider routing. It only coordinates one
already-compiled QueryPlan: total deadline accounting, duplicate-call joining,
short terminal-error coalescing, symbol de-duplication and deterministic
chunking. Provider switching therefore cannot be introduced by an optimization
primitive.
"""

from __future__ import annotations

import contextlib
import copy
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from .errors import (
    CommandOffline,
    ReadTimeout,
    SourceDeprecated,
    SourceUnavailable,
    TdxError,
    ValidationError,
)

__all__ = [
    "ExecutionBudget",
    "SingleFlight",
    "BatchPlan",
    "BatchPlanner",
]

T = TypeVar("T")


def _record_singleflight(event: str) -> None:
    with contextlib.suppress(Exception):
        from .observability.planned import record_singleflight

        record_singleflight(event)


def _default_negative_predicate(exc: BaseException) -> bool:
    """Return whether an error is stable enough for very short coalescing."""

    return isinstance(exc, (CommandOffline, SourceDeprecated))


def _clone_error(exc: BaseException) -> BaseException:
    """Return an independent exception object for another caller."""

    try:
        return copy.deepcopy(exc)
    except Exception:
        if isinstance(exc, TdxError):
            return type(exc)(
                exc.message,
                code=exc.code,
                advice=exc.advice,
                context=dict(exc.context),
                cause=exc.cause,
            )
        return SourceUnavailable(
            "cached terminal provider error",
            context={"negative_cache": True, "fallback": False},
        )


@dataclass(slots=True)
class ExecutionBudget:
    """One total monotonic deadline shared by the whole logical query."""

    deadline_ns: int
    max_attempts: int = 1
    attempts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_deadline_ms(cls, deadline_ms: int, *, max_attempts: int = 1) -> "ExecutionBudget":
        if deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0",
                context={"deadline_ms": deadline_ms},
            )
        if max_attempts <= 0:
            raise ValidationError(
                "max_attempts 必须大于 0",
                context={"max_attempts": max_attempts},
            )
        return cls(
            deadline_ns=time.monotonic_ns() + int(deadline_ms * 1_000_000),
            max_attempts=max_attempts,
        )

    def remaining_ns(self) -> int:
        return max(0, self.deadline_ns - time.monotonic_ns())

    def remaining_s(self) -> float:
        return self.remaining_ns() / 1_000_000_000

    def ensure_remaining(self, phase: str) -> None:
        if self.remaining_ns() <= 0:
            raise ReadTimeout(
                "查询总 deadline 已耗尽",
                context={"phase": phase, "deadline_scope": "query"},
            )

    def begin_attempt(self, phase: str = "provider_request") -> None:
        self.ensure_remaining(phase)
        with self._lock:
            if self.attempts >= self.max_attempts:
                raise ReadTimeout(
                    "查询执行次数已耗尽",
                    context={
                        "phase": phase,
                        "attempts": self.attempts,
                        "max_attempts": self.max_attempts,
                        "deadline_scope": "query",
                    },
                )
            self.attempts += 1


@dataclass(slots=True)
class _Flight(Generic[T]):
    event: threading.Event = field(default_factory=threading.Event)
    result: T | None = None
    error: BaseException | None = None
    waiters: int = 0


@dataclass(slots=True)
class _NegativeEntry:
    expires_at: float
    error: BaseException


class SingleFlight:
    """Coalesce identical work without inheriting another caller's deadline.

    One semantic key normally has one active leader. Followers first join that
    leader even when their absolute deadlines differ, maximizing coalescing for
    the common success path. If the leader fails specifically because *its own*
    query deadline was exhausted and a follower still has budget, that follower
    re-enters SingleFlight with only its remaining budget. The first such retry
    becomes the next leader and the other followers join it, avoiding both
    deadline inheritance and a retry stampede.

    Successful followers receive defensive deep copies so independent callers do
    not share mutable ``QueryResult``/list/model instances.

    A very short negative cache coalesces only stable terminal failures. The key
    remains the full QueryFingerprint supplied by the planner, so cached errors
    cannot cross Provider, Channel, Capability or window semantics. Transient
    transport/provider availability failures are deliberately excluded.
    """

    def __init__(
        self,
        *,
        negative_ttl: float = 1.0,
        negative_predicate: Callable[[BaseException], bool] | None = None,
    ) -> None:
        if negative_ttl < 0:
            raise ValueError("negative_ttl must be >= 0")
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight[Any]] = {}
        self._negative: dict[str, _NegativeEntry] = {}
        self.negative_ttl = float(negative_ttl)
        self._negative_predicate = negative_predicate or _default_negative_predicate
        self.leaders = 0
        self.joins = 0
        self.deadline_bypasses = 0
        self.negative_hits = 0
        self.negative_stores = 0

    @staticmethod
    def _absolute_deadline(timeout: float | None) -> int | None:
        if timeout is None:
            return None
        return time.monotonic_ns() + max(0, int(timeout * 1_000_000_000))

    @staticmethod
    def _remaining_seconds(deadline_ns: int | None) -> float | None:
        if deadline_ns is None:
            return None
        return max(0.0, (deadline_ns - time.monotonic_ns()) / 1_000_000_000)

    @staticmethod
    def _is_query_deadline(error: BaseException) -> bool:
        return isinstance(error, ReadTimeout) and error.context.get("deadline_scope") == "query"

    def clear_negative(self, key: str | None = None) -> None:
        """Clear one or all terminal-error entries without touching active flights."""

        with self._lock:
            if key is None:
                self._negative.clear()
            else:
                self._negative.pop(key, None)

    def _negative_lookup_locked(self, key: str, now: float) -> BaseException | None:
        entry = self._negative.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            del self._negative[key]
            return None
        self.negative_hits += 1
        return _clone_error(entry.error)

    def _negative_store(self, key: str, exc: BaseException) -> None:
        if self.negative_ttl <= 0 or not self._negative_predicate(exc):
            return
        snapshot = _clone_error(exc)
        with self._lock:
            self._negative[key] = _NegativeEntry(
                expires_at=time.monotonic() + self.negative_ttl,
                error=snapshot,
            )
            self.negative_stores += 1
        _record_singleflight("negative_store")

    def do(
        self,
        key: str,
        fn: Callable[[], T],
        *,
        timeout: float | None = None,
    ) -> T:
        if timeout is not None and timeout <= 0:
            _record_singleflight("timeout")
            raise ReadTimeout(
                "同指纹请求进入 SingleFlight 前 query deadline 已耗尽",
                context={"phase": "singleflight_enter", "deadline_scope": "query"},
            )

        caller_deadline_ns = self._absolute_deadline(timeout)
        cached_error: BaseException | None = None
        with self._lock:
            cached_error = self._negative_lookup_locked(key, time.monotonic())
            if cached_error is not None:
                flight = None
                leader = False
            else:
                flight = self._flights.get(key)
                if flight is None:
                    flight = _Flight[T]()
                    self._flights[key] = flight
                    self.leaders += 1
                    leader = True
                    _record_singleflight("leader")
                else:
                    flight.waiters += 1
                    self.joins += 1
                    leader = False
                    _record_singleflight("join")

        if cached_error is not None:
            _record_singleflight("negative_hit")
            raise cached_error
        if flight is None:  # defensive: only possible when cached_error is set
            raise RuntimeError("SingleFlight internal state error")

        if leader:
            try:
                flight.result = fn()
            except BaseException as exc:
                flight.error = exc
                self._negative_store(key, exc)
            else:
                with self._lock:
                    self._negative.pop(key, None)
            finally:
                # Remove the completed flight before waking followers. A follower
                # that must retry after the leader's query deadline can then create
                # or join the next flight instead of rejoining this failed object.
                with self._lock:
                    current = self._flights.get(key)
                    if current is flight:
                        del self._flights[key]
                flight.event.set()
            if flight.error is not None:
                raise flight.error
            return cast(T, flight.result)

        remaining = self._remaining_seconds(caller_deadline_ns)
        completed = flight.event.wait(timeout=remaining)
        if not completed:
            _record_singleflight("timeout")
            raise ReadTimeout(
                "等待同指纹上游请求超过 query deadline",
                context={
                    "phase": "singleflight_wait",
                    "singleflight": True,
                    "deadline_scope": "query",
                },
            )
        if flight.error is not None:
            remaining = self._remaining_seconds(caller_deadline_ns)
            if self._is_query_deadline(flight.error) and (remaining is None or remaining > 0):
                with self._lock:
                    self.deadline_bypasses += 1
                _record_singleflight("deadline_bypass")
                return self.do(key, fn, timeout=remaining)
            raise flight.error
        return copy.deepcopy(cast(T, flight.result))


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """Stable de-duplication plan preserving caller symbol order."""

    original: tuple[str, ...]
    unique: tuple[str, ...]
    positions: tuple[int, ...]

    @property
    def deduplicated(self) -> bool:
        return len(self.unique) != len(self.original)

    def fanout(self, items: list[T], *, allow_partial: bool = False) -> list[T]:
        if len(items) != len(self.unique):
            if allow_partial:
                return list(items)
            raise SourceUnavailable(
                "Provider 返回的批量结果数量与请求不一致",
                context={
                    "requested_unique": len(self.unique),
                    "received": len(items),
                    "partial": bool(items),
                },
            )
        return [items[index] for index in self.positions]


class BatchPlanner:
    """De-duplicate once, chunk by declared Provider limit, preserve fan-out."""

    @staticmethod
    def symbols(symbols: tuple[str, ...] | list[str]) -> BatchPlan:
        original = tuple(symbols)
        index: dict[str, int] = {}
        unique: list[str] = []
        positions: list[int] = []
        for symbol in original:
            pos = index.get(symbol)
            if pos is None:
                pos = len(unique)
                index[symbol] = pos
                unique.append(symbol)
            positions.append(pos)
        return BatchPlan(
            original=original,
            unique=tuple(unique),
            positions=tuple(positions),
        )

    @staticmethod
    def chunks(items: tuple[T, ...] | list[T], limit: int | None) -> tuple[tuple[T, ...], ...]:
        values = tuple(items)
        if not values:
            return ()
        if limit is None:
            return (values,)
        if limit <= 0:
            raise ValidationError(
                "batch limit 必须大于 0",
                context={"batch_limit": limit},
            )
        return tuple(values[index : index + limit] for index in range(0, len(values), limit))
