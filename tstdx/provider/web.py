from __future__ import annotations

from typing import Any

from .base import Provider


class WebProvider(Provider):
    """Adapter for HTTP/web market data sources."""

    name = "web"

    def __init__(self, source: Any | None = None) -> None:
        self.source = source

    def supports(self, operation: str) -> bool:
        return self.source is not None and callable(getattr(self.source, operation, None))

    def query(self, request: Any) -> Any:
        if self.source is None:
            raise RuntimeError("web source is not configured")
        operation = getattr(request, "operation", None)
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.source, operation, None)
        if method is None:
            raise AttributeError(f"unsupported web operation: {operation}")
        return method(*args, **params)
