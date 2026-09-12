from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(slots=True)
class ExecutionContext:
    """Shared state passed through runtime execution stages."""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    timeout: float | None = None
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
