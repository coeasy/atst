from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import Provider


class ProviderRouter:
    """Registry and fallback selector for runtime providers."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if not provider.name:
            raise ValueError("provider name must not be empty")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def query(self, name: str, request: Any) -> Any:
        provider = self.get(name)
        if provider is None:
            raise KeyError(f"unknown provider: {name}")
        if not provider.health():
            raise RuntimeError(f"provider is unhealthy: {name}")
        return provider.query(request)

    def query_first(self, request: Any, names: Iterable[str] | None = None) -> tuple[str, Any]:
        candidates = tuple(names) if names is not None else self.names()
        if not candidates:
            raise RuntimeError("no providers are registered")

        errors: dict[str, str] = {}
        for name in candidates:
            provider = self.get(name)
            if provider is None:
                errors[name] = "not registered"
                continue
            try:
                if not provider.health():
                    errors[name] = "unhealthy"
                    continue
                return name, provider.query(request)
            except Exception as exc:  # provider boundary: aggregate and continue
                errors[name] = f"{type(exc).__name__}: {exc}"

        detail = "; ".join(f"{name}={error}" for name, error in errors.items())
        raise RuntimeError(f"all providers failed: {detail}")
