from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryRequest:
    """Canonical runtime request object.

    This object decouples public APIs from providers and execution engines.
    """

    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_metadata(self, **values: Any) -> "QueryRequest":
        metadata = dict(self.metadata)
        metadata.update(values)
        return QueryRequest(
            operation=self.operation,
            params=dict(self.params),
            metadata=metadata,
        )
