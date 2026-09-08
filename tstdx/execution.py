# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Execution primitives shared by sync/async market-data entrypoints.

This module deliberately contains no Provider routing. It only coordinates one
already-compiled QueryPlan: total deadline accounting, duplicate-call joining,
symbol de-duplication and deterministic chunking. Provider switching therefore
cannot be introduced by an optimization primitive.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, cast

from .errors import ReadTimeout, SourceUnavailable, ValidationError

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


class SingleFlight:
    """Join concurrent calls with the exact same semantic fingerprint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight[Any]] = {}
        self.leaders = 0
        self.joins = 0

    def do(
        self,
        key: str,
        fn: Callable[[], T],
        *,
        timeout: float | None = None,
    ) -> T:
        with self._lock:
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

        if leader:
            try:
                flight.result = fn()
            except BaseException as exc:
                flight.error = exc
            finally:
                flight.event.set()
                with self._lock:
                    current = self._flights.get(key)
                    if current is flight:
                        del self._flights[key]
            if flight.error is not None:
                raise flight.error
            return cast(T, flight.result)

        if timeout is not None and timeout <= 0:
            _record_singleflight("timeout")
            raise ReadTimeout(
                "等待同指纹请求时 query deadline 已耗尽",
                context={"phase": "singleflight_wait", "deadline_scope": "query"},
            )
        completed = flight.event.wait(timeout=timeout)
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
            raise flight.error
        return cast(T, flight.result)


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
