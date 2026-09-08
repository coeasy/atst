# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Auditable partial-result contracts for batched market-data requests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .error_envelope import ErrorEnvelope

__all__ = ["BatchResult"]

T = TypeVar("T")
_BATCH_ERROR_STATUSES = frozenset({"failed", "missing", "not_attempted"})


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ErrorEnvelope):
        return value.to_dict()
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return str(value)


def _copy_error(error: ErrorEnvelope) -> ErrorEnvelope:
    """Detach mutable envelope context from the producer/caller."""

    return replace(error, context=copy.deepcopy(dict(error.context)))


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    """Batch successes plus per-symbol errors without losing provenance.

    ``items`` follow original request order for symbols that succeeded. Duplicate
    requested symbols are fanned out in the same way as the strict API.
    ``errors`` is keyed by canonical symbol and therefore remains compact when a
    missing symbol was requested more than once.

    ``partial`` is a derived truth: any per-symbol error means the batch is
    partial, and a partial batch must expose at least one auditable error. Error
    envelopes are defensively copied and the mapping is made read-only so later
    producer/caller mutation cannot rewrite an already-returned batch audit.
    ``__deepcopy__`` reconstructs the immutable mapping explicitly so
    SingleFlight/query-many followers still receive independent results.
    """

    items: tuple[T, ...]
    errors: Mapping[str, ErrorEnvelope] = field(default_factory=dict)
    requested: tuple[str, ...] = ()
    partial: bool = False
    meta: Any | None = None

    def __post_init__(self) -> None:
        items = tuple(self.items)
        requested = tuple(self.requested)
        raw_errors = dict(self.errors)
        for symbol, error in raw_errors.items():
            if not isinstance(error, ErrorEnvelope):
                raise TypeError(f"BatchResult error for {symbol!r} must be ErrorEnvelope")
        if bool(raw_errors) != bool(self.partial):
            raise ValueError("BatchResult.partial 必须与逐标的 errors 是否存在完全一致")
        if requested:
            requested_set = set(requested)
            unknown = sorted(set(raw_errors) - requested_set)
            if unknown:
                raise ValueError(f"BatchResult.errors 包含未请求标的: {unknown!r}")
            if len(items) > len(requested):
                raise ValueError("BatchResult.items 数量不能超过 requested")

        copied_errors = {
            str(symbol): _copy_error(error) for symbol, error in raw_errors.items()
        }
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "requested", requested)
        object.__setattr__(self, "errors", MappingProxyType(copied_errors))
        object.__setattr__(self, "meta", copy.deepcopy(self.meta))

    def __deepcopy__(self, memo: dict[int, Any]) -> BatchResult[T]:
        """Deep-copy through the constructor instead of copying mappingproxy."""

        existing = memo.get(id(self))
        if existing is not None:
            return existing
        copied = type(self)(
            items=copy.deepcopy(self.items, memo),
            errors={
                symbol: copy.deepcopy(error, memo)
                for symbol, error in self.errors.items()
            },
            requested=tuple(self.requested),
            partial=self.partial,
            meta=copy.deepcopy(self.meta, memo),
        )
        memo[id(self)] = copied
        return copied

    @property
    def success(self) -> bool:
        return not self.errors

    def status_for(self, symbol: str) -> str:
        """Return ``ok/failed/missing/not_attempted`` for one requested symbol."""

        key = str(symbol)
        if self.requested and key not in self.requested:
            raise KeyError(key)
        error = self.errors.get(key)
        if error is None:
            return "ok"
        status = error.context.get("batch_status")
        if isinstance(status, str) and status in _BATCH_ERROR_STATUSES:
            return status
        return "failed"

    @property
    def status_counts(self) -> Mapping[str, int]:
        counts = {"ok": 0, "failed": 0, "missing": 0, "not_attempted": 0}
        symbols = tuple(dict.fromkeys(self.requested or tuple(self.errors)))
        for symbol in symbols:
            counts[self.status_for(symbol)] += 1
        return MappingProxyType(counts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": _plain(self.items),
            "errors": {symbol: error.to_dict() for symbol, error in self.errors.items()},
            "requested": list(self.requested),
            "partial": self.partial,
            "meta": _plain(self.meta),
        }
