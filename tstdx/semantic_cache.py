# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Semantic L1 cache keyed by canonical QueryFingerprint.

This cache is intentionally opt-in at the planned service layer. A query with
``max_age=None`` never consults it. Entries are isolated by the full semantic
fingerprint, which includes Provider, Channel, symbols/window, adjustment,
freshness policy and schema version.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

__all__ = ["CacheEntry", "SemanticQueryCache"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheEntry(Generic[T]):
    value: T
    stored_monotonic_ns: int


class SemanticQueryCache:
    """Thread-safe bounded LRU for exact semantic query results."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self.max_entries = int(max_entries)
        self._lock = threading.RLock()
        self._data: OrderedDict[str, CacheEntry[Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0

    def get(self, key: str, *, max_age: float) -> Any | None:
        """Return a defensive copy only when the entry age is within max_age."""
        if max_age <= 0:
            with self._lock:
                self.misses += 1
            return None
        now = time.monotonic_ns()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            age = (now - entry.stored_monotonic_ns) / 1_000_000_000
            if age > max_age:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            value = entry.value
        return copy.deepcopy(value)

    def put(self, key: str, value: Any) -> None:
        copied = copy.deepcopy(value)
        stored = CacheEntry(
            value=copied,
            stored_monotonic_ns=time.monotonic_ns(),
        )
        with self._lock:
            self._data[key] = stored
            self._data.move_to_end(key)
            self.writes += 1
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                self.evictions += 1

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
