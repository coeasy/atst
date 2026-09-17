# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Compatibility adapter between Runtime identities and cache backends.

During the Provider Isolation migration, Runtime uses RuntimeCacheIdentity as
its canonical cache boundary while existing cache implementations can continue
to receive compatible keys through this adapter.
"""

from __future__ import annotations

from typing import Any

from .runtime_identity import RuntimeCacheIdentity


class RuntimeCacheAdapter:
    """Translate Runtime cache identities into backend operations."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    @staticmethod
    def _key(identity: RuntimeCacheIdentity) -> tuple[str, str, str, str]:
        return (
            identity.provider,
            identity.channel,
            identity.capability,
            identity.fingerprint,
        )

    def get(self, identity: RuntimeCacheIdentity) -> Any:
        return self.backend.get(self._key(identity))

    def put(
        self,
        identity: RuntimeCacheIdentity,
        value: Any,
        *,
        ttl: float | None = None,
    ) -> None:
        self.backend.put(
            self._key(identity),
            value,
            ttl=ttl,
        )

    def invalidate(self, identity: RuntimeCacheIdentity) -> None:
        self.backend.invalidate(self._key(identity))
