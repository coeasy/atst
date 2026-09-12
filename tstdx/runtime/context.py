from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(slots=True)
class ExecutionContext:
    """Shared context passed through runtime execution stages."""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    timeout: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)
