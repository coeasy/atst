from __future__ import annotations

from typing import Any

from ..providers import PROVIDERS
from .base import Provider


class LocalProvider(Provider):
    """Dynamic executor for the canonical ``local_vipdoc`` Provider."""

    name = "local_vipdoc"

    def __init__(self, reader: Any | None = None) -> None:
        self.reader = reader

    def supports(self, operation: str) -> bool:
        return (
            self.reader is not None
            and PROVIDERS.supports(self.name, operation)
            and callable(getattr(self.reader, operation, None))
        )

    def query(self, request: Any) -> Any:
        if self.reader is None:
            raise RuntimeError("local vipdoc reader is not configured")
        operation = getattr(request, "operation", None)
        if not isinstance(operation, str) or not operation:
            raise ValueError("runtime request operation must be a non-empty string")
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.reader, operation, None)
        if method is None:
            raise AttributeError(f"unsupported local_vipdoc operation: {operation}")
        return method(*args, **params)
