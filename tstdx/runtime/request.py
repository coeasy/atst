# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QueryRequest:
    """Runtime boundary request preserving the caller's original call shape.

    This is intentionally *not* the canonical market query model. Semantic
    market identity remains owned by :class:`tstdx.query.QuerySpec` and
    :class:`tstdx.query.QueryFingerprint`.
    """

    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    args: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def request_key(self) -> str:
        """Stable diagnostic identity for the runtime boundary request.

        This key is suitable for tracing/single-flight bookkeeping only. It is
        not a semantic market-data cache key because Provider/Channel identity
        is deliberately absent. Semantic caches must use ``QueryFingerprint``.
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

    def with_metadata(self, **values: Any) -> QueryRequest:
        metadata = dict(self.metadata)
        metadata.update(values)
        return QueryRequest(
            operation=self.operation,
            params=dict(self.params),
            metadata=metadata,
            args=tuple(self.args),
        )
