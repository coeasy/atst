# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Compatibility adapter between Runtime identities and QueryPlan cache backends.

The v13 semantic caches still need the full QueryPlan for freshness policy and
result reconstruction. RuntimeCacheIdentity remains the canonical isolation
boundary; this adapter verifies that the identity belongs to the supplied plan
before delegating to the existing backend.
"""

from __future__ import annotations

from typing import Any

from .query import QueryPlan
from .runtime_identity import RuntimeCacheIdentity, cache_identity_from_plan


class RuntimeCacheIdentityMismatchError(RuntimeError):
    """Raised when a cache identity does not belong to the supplied QueryPlan."""


class RuntimeCacheAdapter:
    """Guard QueryPlan cache operations with a provider-aware Runtime identity."""

    def __init__(self, backend: Any) -> None:
        self.backend = backend

    @staticmethod
    def _validate(identity: RuntimeCacheIdentity, plan: QueryPlan) -> None:
        expected = cache_identity_from_plan(plan)
        if identity.key() != expected.key():
            raise RuntimeCacheIdentityMismatchError(
                "runtime cache identity does not match QueryPlan"
            )

    def get(
        self,
        identity: RuntimeCacheIdentity,
        plan: QueryPlan,
        *,
        now_ns: int | None = None,
    ) -> Any:
        self._validate(identity, plan)
        if now_ns is None:
            return self.backend.get(plan)
        return self.backend.get(plan, now_ns=now_ns)

    def put(
        self,
        identity: RuntimeCacheIdentity,
        plan: QueryPlan,
        value: Any,
        *,
        ttl: float | None = None,
        now_ns: int | None = None,
    ) -> None:
        self._validate(identity, plan)
        kwargs: dict[str, Any] = {"ttl": ttl}
        if now_ns is not None:
            kwargs["now_ns"] = now_ns
        self.backend.put(plan, value, **kwargs)

    def invalidate(
        self,
        identity: RuntimeCacheIdentity,
        plan: QueryPlan,
    ) -> bool:
        self._validate(identity, plan)
        return bool(self.backend.invalidate(plan))
