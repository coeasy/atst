from __future__ import annotations

from typing import Any

from .base import Provider


class TdxProvider(Provider):
    """Adapter for existing TDX clients.

    Keeps protocol/client implementations isolated from v14 runtime.
    """

    name = "tdx"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def query(self, request: Any) -> Any:
        if self.client is None:
            raise RuntimeError("tdx client is not configured")
        operation = getattr(request, "operation", None)
        params = getattr(request, "params", {})
        method = getattr(self.client, operation, None)
        if method is None:
            raise AttributeError(f"unsupported tdx operation: {operation}")
        return method(**params)
