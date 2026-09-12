from __future__ import annotations

from typing import Any

from .base import Provider


class CacheProvider(Provider):
    """Adapter for cache/golden replay providers."""

    name = "cache"

    def __init__(self, store: Any | None = None) -> None:
        self.store = store

    def query(self, request: Any) -> Any:
        if self.store is None:
            raise RuntimeError("cache store is not configured")
        key = getattr(request, "cache_key", None)
        if hasattr(self.store, "get"):
            return self.store.get(key)
        return None
