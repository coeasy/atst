from __future__ import annotations

from typing import Any

from ..providers import PROVIDERS
from .base import Provider


class TdxProvider(Provider):
    """Dynamic executor for the canonical ``tdx`` Provider."""

    name = "tdx"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def supports(self, operation: str) -> bool:
        return (
            self.client is not None
            and PROVIDERS.supports(self.name, operation)
            and callable(getattr(self.client, operation, None))
        )

    def query(self, request: Any) -> Any:
        if self.client is None:
            raise RuntimeError("tdx client is not configured")
        operation = getattr(request, "operation", None)
        if not isinstance(operation, str) or not operation:
            raise ValueError("runtime request operation must be a non-empty string")
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.client, operation, None)
        if method is None:
            raise AttributeError(f"unsupported tdx operation: {operation}")
        return method(*args, **params)
