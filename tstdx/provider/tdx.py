from __future__ import annotations

from typing import Any

from .base import Provider


class TdxProvider(Provider):
    """Adapter for existing TDX clients."""

    name = "tdx"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def supports(self, operation: str) -> bool:
        return self.client is not None and callable(getattr(self.client, operation, None))

    def query(self, request: Any) -> Any:
        if self.client is None:
            raise RuntimeError("tdx client is not configured")
        operation = getattr(request, "operation", None)
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.client, operation, None)
        if method is None:
            raise AttributeError(f"unsupported tdx operation: {operation}")
        return method(*args, **params)
