from __future__ import annotations

from typing import Any

from .base import Provider


class ProviderRouter:
    """Select execution provider for runtime nodes.

    Initial implementation keeps explicit registration. Future versions add
    health ranking, circuit state and cost based planning.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def query(self, name: str, request: Any) -> Any:
        provider = self.get(name)
        if provider is None:
            raise KeyError(f"unknown provider: {name}")
        return provider.query(request)
