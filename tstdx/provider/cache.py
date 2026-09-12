from __future__ import annotations

from typing import Any

from .base import Provider


class CacheProvider(Provider):
    """Adapter for cache/golden replay stores.

    A cache miss is surfaced as ``LookupError`` so ProviderRouter can continue
    to the next backend instead of treating ``None`` as a successful response.
    """

    name = "cache"

    def __init__(self, store: Any | None = None) -> None:
        self.store = store

    def query(self, request: Any) -> Any:
        if self.store is None:
            raise RuntimeError("cache store is not configured")
        if not hasattr(self.store, "get"):
            raise TypeError("cache store must provide get(key)")

        value = self.store.get(request.cache_key)
        if value is None:
            raise LookupError(f"cache miss: {request.cache_key}")
        return value
