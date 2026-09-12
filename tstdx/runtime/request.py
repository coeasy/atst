from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryRequest:
    """Canonical runtime request object.

    Positional and keyword arguments are preserved so facade/client method
    signatures can migrate behind the runtime without semantic rewrites.
    """

    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    args: tuple[Any, ...] = field(default_factory=tuple)

    def with_metadata(self, **values: Any) -> "QueryRequest":
        metadata = dict(self.metadata)
        metadata.update(values)
        return QueryRequest(
            operation=self.operation,
            params=dict(self.params),
            metadata=metadata,
            args=tuple(self.args),
        )
