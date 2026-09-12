from __future__ import annotations

from typing import Any

from .base import Provider


class LocalProvider(Provider):
    """Adapter for local vipdoc/readers."""

    name = "local"

    def __init__(self, reader: Any | None = None) -> None:
        self.reader = reader

    def query(self, request: Any) -> Any:
        if self.reader is None:
            raise RuntimeError("local reader is not configured")
        operation = getattr(request, "operation", None)
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.reader, operation, None)
        if method is None:
            raise AttributeError(f"unsupported local operation: {operation}")
        return method(*args, **params)
