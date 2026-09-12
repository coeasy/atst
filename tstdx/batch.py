# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Batch/SingleFlight/negative-cache runtime primitives."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .errors import SourceUnavailable, TdxError
from .query import QueryPlan

__all__ = ["BatchItem", "BatchResult", "SingleFlight", "NegativeCache"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class BatchItem(Generic[T]):
    status: str
    value: T | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "failed", "missing", "not_attempted"}:
            raise ValueError(f"invalid batch status: {self.status!r}")
        if self.status == "ok" and self.error is not None:
            raise ValueError("successful batch item cannot carry error")


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    items: Mapping[str, BatchItem[T]]

    @classmethod
    def build(cls, items: Mapping[str, BatchItem[T]]) -> "BatchResult[T]":
        return cls(MappingProxyType(copy.deepcopy(dict(items))))

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(key for key, item in self.items.items() if item.status == "failed")

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(key for key, item in self.items.items() if item.status == "missing")

    @property
    def not_attempted(self) -> tuple[str, ...]:
        return tuple(key for key, item in self.items.items() if item.status == "not_attempted")

    def __deepcopy__(self, memo: dict[int, Any]) -> "BatchResult[T]":
        return BatchResult.build(copy.deepcopy(dict(self.items), memo))


def _clone_exception(exc: Exception) -> Exception:
    if isinstance(exc, TdxError):
        return type(exc)(
            exc.message,
            code=exc.code,
            advice=exc.advice,
            context=copy.deepcopy(exc.context),
            cause=exc.cause,
        )
    try:
        return copy.deepcopy(exc)
    except Exception:
        return RuntimeError(str(exc))


class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.error: Exception | None = None


class SingleFlight:
    """Coalesce concurrent work by full QueryFingerprint.

    The internal result is never returned directly: leader and followers each
    receive independent deep copies, eliminating mutation races between callers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def do(self, plan: QueryPlan, fn: Callable[[], T]) -> T:
        key = plan.fingerprint.value
        with self._lock:
            flight = self._flights.get(key)
            leader = flight is None
            if flight is None:
                flight = _Flight()
                self._flights[key] = flight
        if leader:
            try:
                flight.value = fn()
            except Exception as exc:
                flight.error = exc
            finally:
                flight.event.set()
                with self._lock:
                    self._flights.pop(key, None)
            if flight.error is not None:
                raise _clone_exception(flight.error)
            return copy.deepcopy(flight.value)

        flight.event.wait()
        if flight.error is not None:
            raise _clone_exception(flight.error)
        return copy.deepcopy(flight.value)


@dataclass(frozen=True, slots=True)
class _NegativeEntry:
    expires_at_ns: int
    error: Exception


class NegativeCache:
    """Short-lived cache for deterministic terminal failures only."""

    def __init__(self, *, ttl: float = 1.0, maxsize: int = 1024) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.ttl = float(ttl)
        self.maxsize = int(maxsize)
        self._lock = threading.RLock()
        self._data: dict[str, _NegativeEntry] = {}

    @staticmethod
    def cacheable(exc: Exception) -> bool:
        if isinstance(exc, SourceUnavailable):
            return False
        if not isinstance(exc, TdxError):
            return False
        return not exc.advice.retryable

    def get(self, plan: QueryPlan, *, now_ns: int | None = None) -> Exception | None:
        now = time.time_ns() if now_ns is None else int(now_ns)
        key = plan.fingerprint.value
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if now >= entry.expires_at_ns:
                self._data.pop(key, None)
                return None
            return _clone_exception(entry.error)

    def put(self, plan: QueryPlan, exc: Exception, *, now_ns: int | None = None) -> bool:
        if not self.cacheable(exc):
            return False
        now = time.time_ns() if now_ns is None else int(now_ns)
        key = plan.fingerprint.value
        with self._lock:
            if len(self._data) >= self.maxsize and key not in self._data:
                oldest = next(iter(self._data), None)
                if oldest is not None:
                    self._data.pop(oldest, None)
            self._data[key] = _NegativeEntry(
                expires_at_ns=now + int(self.ttl * 1_000_000_000),
                error=_clone_exception(exc),
            )
        return True

    def invalidate(self, plan: QueryPlan) -> bool:
        with self._lock:
            return self._data.pop(plan.fingerprint.value, None) is not None
