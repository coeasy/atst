# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Auditable partial-result contracts for batched market-data requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, TypeVar

from .error_envelope import ErrorEnvelope

__all__ = ["BatchResult"]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    """Batch successes plus per-symbol errors without losing provenance.

    ``items`` follow original request order for symbols that succeeded. Duplicate
    requested symbols are fanned out in the same way as the strict API.
    ``errors`` is keyed by canonical symbol and therefore remains compact when a
    missing symbol was requested more than once.
    """

    items: tuple[T, ...]
    errors: Mapping[str, ErrorEnvelope] = field(default_factory=dict)
    requested: tuple[str, ...] = ()
    partial: bool = False
    meta: Any | None = None

    @property
    def success(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [
                item.to_dict() if hasattr(item, "to_dict") and callable(item.to_dict) else item
                for item in self.items
            ],
            "errors": {symbol: error.to_dict() for symbol, error in self.errors.items()},
            "requested": list(self.requested),
            "partial": self.partial,
            "meta": self.meta,
        }
