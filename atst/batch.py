# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical batch audit contracts.

The batch path is one loop in :meth:`atst.runtime.kernel.UnifiedRuntime.quotes_batch`:
it expands the requested symbols into independent single-Provider requests and
records each outcome here. There is no separate batch request envelope, no
coalescing layer and no negative cache — one symbol, one direct Provider call.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .error_envelope import ErrorEnvelope

__all__ = ["BatchItem", "BatchResult"]

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
        if self.status == "ok" and self.value is None:
            raise ValueError("successful batch item requires value")
        if self.status != "ok" and self.value is not None:
            raise ValueError("non-success batch item cannot carry value")


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    """Auditable outcome of one batch request.

    ``items`` carries either a ``{symbol: BatchItem}`` mapping (what
    :meth:`build` and :meth:`atst.runtime.kernel.UnifiedRuntime.quotes_batch`
    produce) or a plain value sequence.  ``errors`` maps a requested symbol to
    the safe :class:`~atst.error_envelope.ErrorEnvelope` that explains why it
    is absent, and ``partial`` follows from — and must agree with — whether
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
            object.__setattr__(self, "items", MappingProxyType(copy.deepcopy(dict(items))))
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
            MappingProxyType({key: copy.deepcopy(value) for key, value in errors.items()}),
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
            # 保留 ``items`` 的形态：映射视图按符号建键，值序列保持元组。
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
        return tuple(key for key, status in self._status_map().items() if status == "not_attempted")

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
