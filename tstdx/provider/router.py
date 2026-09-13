# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .base import Provider

ProviderAttempts = list[dict[str, str]]


class ProviderRouter:
    """Registry and fallback selector for runtime providers."""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if not provider.name:
            raise ValueError("provider name must not be empty")
        if provider.name in self._providers:
            raise ValueError(f"provider is already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._providers)

    @staticmethod
    def _operation(request: Any) -> str:
        return str(getattr(request, "operation", "") or "")

    @staticmethod
    def _record(
        attempts: ProviderAttempts | None,
        name: str,
        status: str,
        *,
        detail: str = "",
    ) -> None:
        if attempts is None:
            return
        item = {"provider": name, "status": status}
        if detail:
            item["detail"] = detail
        attempts.append(item)

    def query(
        self,
        name: str,
        request: Any,
        *,
        attempts: ProviderAttempts | None = None,
    ) -> Any:
        provider = self.get(name)
        if provider is None:
            self._record(attempts, name, "not_registered")
            raise KeyError(f"unknown provider: {name}")

        operation = self._operation(request)
        if not provider.supports(operation):
            self._record(attempts, name, "unsupported", detail=operation)
            raise AttributeError(f"provider {name!r} does not support operation: {operation}")
        if not provider.health():
            self._record(attempts, name, "unhealthy")
            raise RuntimeError(f"provider is unhealthy: {name}")

        try:
            result = provider.query(request)
        except Exception as exc:
            self._record(
                attempts,
                name,
                "failed",
                detail=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._record(attempts, name, "selected")
        return result

    def query_first(
        self,
        request: Any,
        names: Iterable[str] | None = None,
        *,
        attempts: ProviderAttempts | None = None,
    ) -> tuple[str, Any]:
        candidates = tuple(names) if names is not None else self.names()
        if not candidates:
            raise RuntimeError("no providers are registered")

        operation = self._operation(request)
        errors: dict[str, str] = {}
        for name in candidates:
            provider = self.get(name)
            if provider is None:
                errors[name] = "not registered"
                self._record(attempts, name, "not_registered")
                continue
            if not provider.supports(operation):
                errors[name] = "unsupported"
                self._record(attempts, name, "unsupported", detail=operation)
                continue
            try:
                if not provider.health():
                    errors[name] = "unhealthy"
                    self._record(attempts, name, "unhealthy")
                    continue
                result = provider.query(request)
            except Exception as exc:  # provider boundary: aggregate and continue
                detail = f"{type(exc).__name__}: {exc}"
                errors[name] = detail
                self._record(attempts, name, "failed", detail=detail)
                continue
            self._record(attempts, name, "selected")
            return name, result

        detail = "; ".join(f"{name}={error}" for name, error in errors.items())
        raise RuntimeError(f"all providers failed: {detail}")
