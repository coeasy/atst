# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Auditable partial-result contracts for batched market-data requests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from .error_envelope import ErrorEnvelope

__all__ = ["BatchResult"]

T = TypeVar("T")


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


@dataclass(frozen=True, slots=True)
class BatchResult(Generic[T]):
    """Batch successes plus per-symbol errors without losing provenance.

    ``items`` follow original request order for symbols that succeeded. Duplicate
    requested symbols are fanned out in the same way as the strict API.
    ``errors`` is keyed by canonical symbol and therefore remains compact when a
    missing symbol was requested more than once.

    ``partial`` is a derived truth: any per-symbol error means the batch is
    partial, and a partial batch must expose at least one auditable error. The
    input mapping is defensively copied so later caller mutation cannot rewrite a
    result that has already been returned or serialized.
    """

    items: tuple[T, ...]
    errors: Mapping[str, ErrorEnvelope] = field(default_factory=dict)
    requested: tuple[str, ...] = ()
    partial: bool = False
    meta: Any | None = None

    def __post_init__(self) -> None:
        errors = dict(self.errors)
        if bool(errors) != bool(self.partial):
            raise ValueError("BatchResult.partial 必须与逐标的 errors 是否存在完全一致")
        object.__setattr__(self, "errors", errors)

    @property
    def success(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": _plain(self.items),
            "errors": {symbol: error.to_dict() for symbol, error in self.errors.items()},
            "requested": list(self.requested),
            "partial": self.partial,
            "meta": _plain(self.meta),
        }
