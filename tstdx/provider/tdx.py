# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from typing import Any

from ..providers import PROVIDERS
from .base import Provider


class TdxProvider(Provider):
    """Dynamic executor for the canonical ``tdx`` Provider."""

    name = "tdx"
    canonical_id = "tdx"

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def capabilities(self) -> frozenset[str]:
        """声明能力 = 注册表能力 ∩ client 实际实现的方法。"""
        registry_caps = super().capabilities()
        if self.client is None:
            return frozenset()
        return frozenset(
            cap
            for cap in registry_caps
            if callable(getattr(self.client, cap, None))
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter": type(self).__name__,
            "client": type(self.client).__name__ if self.client is not None else None,
            "configured": self.client is not None,
        }

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

    def execute(self, request: Any) -> Any:
        return self.query(request)
