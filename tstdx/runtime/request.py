from __future__ import annotations

import hashlib
import json
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

    @property
    def cache_key(self) -> str:
        """Stable key for the semantic request payload.

        Metadata is intentionally excluded because tracing, timeout and routing
        hints must not create distinct cached market-data values.
        """
        payload = json.dumps(
            {
                "operation": self.operation,
                "args": self.args,
                "params": self.params,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=repr,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def with_metadata(self, **values: Any) -> "QueryRequest":
        metadata = dict(self.metadata)
        metadata.update(values)
        return QueryRequest(
            operation=self.operation,
            params=dict(self.params),
            metadata=metadata,
            args=tuple(self.args),
        )
