from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryResponse:
    """Runtime response envelope."""

    success: bool
    data: Any = None
    error: str | None = None
    code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: Any, *, code: str = "", **metadata: Any) -> "QueryResponse":
        return cls(True, data=data, code=code, metadata=metadata)

    @classmethod
    def fail(cls, error: str, *, code: str = "", **metadata: Any) -> "QueryResponse":
        return cls(False, error=error, code=code, metadata=metadata)
