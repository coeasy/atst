# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from typing import Any

from ..providers import PROVIDERS
from .base import Provider


class LocalProvider(Provider):
    """Dynamic executor for the canonical ``local_vipdoc`` Provider."""

    name = "local_vipdoc"
    canonical_id = "local_vipdoc"

    def __init__(self, reader: Any | None = None) -> None:
        self.reader = reader

    def capabilities(self) -> frozenset[str]:
        """声明能力 = 注册表能力 ∩ reader 实际实现的方法。"""
        registry_caps = super().capabilities()
        if self.reader is None:
            return frozenset()
        return frozenset(
            cap
            for cap in registry_caps
            if callable(getattr(self.reader, cap, None))
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": type(self).__name__,
            "reader": type(self.reader).__name__ if self.reader is not None else None,
            "configured": self.reader is not None,
        }

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

    def execute(self, request: Any) -> Any:
        return self.query(request)
