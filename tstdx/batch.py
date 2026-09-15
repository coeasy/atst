# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 batch, SingleFlight and terminal negative-cache primitives."""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .domain.symbol import normalize_symbol
from .error_envelope import ErrorEnvelope
from .errors import SourceUnavailable, TdxError, ValidationError
from .providers import resolve_provider
from .query import CurrentnessMode, QueryPlan

__all__ = ["BatchSpec", "BatchItem", "BatchResult", "SingleFlight", "NegativeCache"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class BatchSpec:
    """Explicit batch request contract; partial success never lives on QuerySpec."""

    capability: str
    symbols: tuple[str, ...]
    provider: str
    currentness: str
    max_age: float | None = None

    @classmethod
    def quotes(
        cls,
        symbols: Sequence[str],
        *,
        provider: str = "tdx",
        currentness: str = CurrentnessMode.LIVE.value,
        max_age: float | None = None,
    ) -> BatchSpec:
        values = tuple(dict.fromkeys(normalize_symbol(item) for item in symbols))
        if not values:
            raise ValidationError("batch quotes 至少需要一个 symbol")
        if max_age is not None and max_age < 0:
            raise ValidationError("max_age 不能为负数")
        return cls(
            capability="quotes",
            symbols=values,
            provider=resolve_provider(provider=provider),
            currentness=str(currentness).strip().lower(),
            max_age=max_age,
        )


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
        if self.status == "ok" and self.value is None:
            raise ValueError("successful batch item requires value")
        if self.status != "ok" and self.value is not None:
            raise ValueError("non-success batch item cannot carry value")


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    """Auditable outcome of one batch request.

    ``items`` carries the **successful values in request order** for the
    canonical (``planned_service``) contract, and a ``{symbol: BatchItem}``
    mapping for the legacy ``build()`` view.  ``errors`` maps a requested symbol
    to the safe :class:`~tstdx.error_envelope.ErrorEnvelope` that explains why it
    is absent, and ``partial`` is derived from — and must agree with — whether
    ``errors`` is non-empty.
    """

    items: tuple[T, ...] | Mapping[str, BatchItem[Any]] = ()
    errors: Mapping[str, ErrorEnvelope] = field(default_factory=dict)
    requested: tuple[str, ...] = ()
    partial: bool = False
    meta: Any = field(default_factory=dict)

    def __post_init__(self) -> None:
        items = self.items
        if isinstance(items, Mapping):
            object.__setattr__(
                self, "items", MappingProxyType(copy.deepcopy(dict(items)))
            )
        elif not isinstance(items, tuple):
            object.__setattr__(self, "items", tuple(items))

        requested = self.requested
        if not isinstance(requested, tuple):
            object.__setattr__(self, "requested", tuple(requested))

        errors = self.errors
        if not isinstance(errors, Mapping):
            raise TypeError("BatchResult.errors 必须是 Mapping[str, ErrorEnvelope]")
        if bool(errors) != bool(self.partial):
            raise ValueError(
                "BatchResult.partial 必须与 errors 是否为空严格一致："
                f"partial={self.partial!r} errors={len(errors)}"
            )
        if requested:
            unexpected = sorted(set(errors) - set(requested))
            if unexpected:
                raise ValueError(f"errors 包含未请求的 symbol: {unexpected}")
        # 防御性拷贝：调用方后续修改传入的映射/上下文不得影响已产出的审计结果。
        object.__setattr__(
            self,
            "errors",
            MappingProxyType(
                {key: copy.deepcopy(value) for key, value in errors.items()}
            ),
        )
        object.__setattr__(self, "meta", _safe_deepcopy(self.meta))

    @classmethod
    def build(
        cls,
        items: Mapping[str, BatchItem[T]],
        *,
        errors: Mapping[str, ErrorEnvelope] | None = None,
        requested: Sequence[str] = (),
        partial: bool | None = None,
        meta: Any = None,
    ) -> BatchResult[T]:
        error_map = dict(errors or {})
        return cls(
            items=dict(items),
            errors=error_map,
            requested=tuple(requested),
            partial=bool(error_map) if partial is None else bool(partial),
            meta={} if meta is None else meta,
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> BatchResult[T]:
        items = self.items
        clone = self.__class__(
            # 保留 ``items`` 的形态：规范契约是值序列，legacy ``build()`` 是符号映射。
            items=dict(items) if isinstance(items, Mapping) else tuple(items),
            errors=dict(self.errors),
            requested=self.requested,
            partial=self.partial,
            meta=_safe_deepcopy(self.meta, memo),
        )
        memo[id(self)] = clone
        return clone

    def _status_map(self) -> dict[str, str]:
        if isinstance(self.items, Mapping):
            return {str(key): item.status for key, item in self.items.items()}
        statuses: dict[str, str] = {}
        for symbol in self.requested:
            if symbol not in statuses:
                statuses[symbol] = self.status_for(symbol)
        return statuses

    def status_for(self, symbol: str) -> str:
        """Return the auditable status of one requested symbol."""

        envelope = self.errors.get(symbol)
        if envelope is not None:
            recorded = envelope.context.get("batch_status")
            if isinstance(recorded, str) and recorded:
                return recorded
            return "failed"
        items = self.items
        if isinstance(items, Mapping):
            item = items.get(symbol)
            if item is not None:
                return item.status
        if symbol in set(self.requested):
            return "ok"
        raise KeyError(symbol)

    @property
    def status_counts(self) -> Mapping[str, int]:
        counts = {"ok": 0, "failed": 0, "missing": 0, "not_attempted": 0}
        for status in self._status_map().values():
            if status in counts:
                counts[status] += 1
            else:  # pragma: no cover - defensive: never silently drop an audit row
                counts[status] = counts.get(status, 0) + 1
        return MappingProxyType(counts)

    @property
    def success(self) -> bool:
        if self.errors:
            return False
        items = self.items
        if isinstance(items, Mapping):
            return all(item.status == "ok" for item in items.values())
        return True

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(key for key, status in self._status_map().items() if status == "failed")

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(key for key, status in self._status_map().items() if status == "missing")

    @property
    def not_attempted(self) -> tuple[str, ...]:
        return tuple(
            key for key, status in self._status_map().items() if status == "not_attempted"
        )

    def to_dict(self) -> dict[str, Any]:
        items = self.items
        if isinstance(items, Mapping):
            values: list[Any] = [
                _serialize_item(item.value) for item in items.values() if item.status == "ok"
            ]
        else:
            values = [_serialize_item(value) for value in items]
        return {
            "items": values,
            "errors": {key: value.to_dict() for key, value in self.errors.items()},
            "requested": list(self.requested),
            "partial": self.partial,
            "meta": _serialize_item(self.meta),
        }


def _safe_deepcopy(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Deep-copy a diagnostic payload, degrading to the original on failure."""

    try:
        return copy.deepcopy(value, memo) if memo is not None else copy.deepcopy(value)
    except Exception:
        return value


def _serialize_item(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:  # pragma: no cover - defensive: never fail serialization
            return value
    if isinstance(value, Mapping):
        return dict(value)
    return value


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
    """Coalesce concurrent work by the complete QueryFingerprint."""

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
    """Short-lived LRU cache for deterministic terminal failures only."""

    def __init__(self, *, ttl: float = 1.0, maxsize: int = 1024) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        self.ttl = float(ttl)
        self.maxsize = int(maxsize)
        self._lock = threading.RLock()
        self._data: OrderedDict[str, _NegativeEntry] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.rejects = 0

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
                self.misses += 1
                return None
            if now >= entry.expires_at_ns:
                self._data.pop(key, None)
                self.misses += 1
                return None
            self._data.move_to_end(key, last=True)
            self.hits += 1
            return _clone_exception(entry.error)

    def put(self, plan: QueryPlan, exc: Exception, *, now_ns: int | None = None) -> bool:
        if not self.cacheable(exc):
            self.rejects += 1
            return False
        now = time.time_ns() if now_ns is None else int(now_ns)
        key = plan.fingerprint.value
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            while len(self._data) >= self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1
            self._data[key] = _NegativeEntry(
                expires_at_ns=now + int(self.ttl * 1_000_000_000),
                error=_clone_exception(exc),
            )
        return True

    def invalidate(self, plan: QueryPlan) -> bool:
        with self._lock:
            return self._data.pop(plan.fingerprint.value, None) is not None

    def metrics(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._data),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "rejects": self.rejects,
            }
