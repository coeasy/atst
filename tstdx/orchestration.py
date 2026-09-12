# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Explicit cross-Provider orchestration above the strict runtime kernel.

Fallback is opt-in and auditable here; :class:`UnifiedRuntime` itself remains
single-Provider and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .error_envelope import to_error_envelope
from .errors import AllSourcesExhausted
from .providers import PROVIDERS, resolve_provider
from .result import QueryResult
from .runtime import UnifiedRuntime

__all__ = [
    "FallbackPolicy",
    "ProviderAttempt",
    "OrchestratedResult",
    "ProviderOrchestrator",
]


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    providers: tuple[str, ...]

    @classmethod
    def build(cls, *providers: str) -> "FallbackPolicy":
        normalized = tuple(resolve_provider(provider=item) for item in providers)
        if not normalized:
            raise ValueError("fallback policy requires at least one Provider")
        if len(normalized) != len(set(normalized)):
            raise ValueError("fallback policy cannot contain duplicate Providers")
        for provider in normalized:
            PROVIDERS.get(provider)
        return cls(normalized)


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    status: str
    code: str | None = None


@dataclass(frozen=True, slots=True)
class OrchestratedResult:
    result: QueryResult[Any]
    attempts: tuple[ProviderAttempt, ...]


class ProviderOrchestrator:
    """Execute an explicit Provider order without changing kernel semantics."""

    def __init__(self, runtime: UnifiedRuntime | None = None) -> None:
        self.runtime = runtime or UnifiedRuntime()

    @staticmethod
    def _mark_fallback(
        result: QueryResult[Any],
        *,
        requested_provider: str,
        fallback: bool,
    ) -> QueryResult[Any]:
        provenance = replace(
            result.meta.provenance,
            requested_provider=requested_provider,
            fallback=fallback,
        )
        return QueryResult(
            data=result.data,
            meta=replace(result.meta, provenance=provenance),
        )

    def quotes(
        self,
        symbols: str | list[str] | tuple[str, ...],
        *,
        policy: FallbackPolicy,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> OrchestratedResult:
        attempts: list[ProviderAttempt] = []
        requested = policy.providers[0]
        for index, provider in enumerate(policy.providers):
            try:
                result = self.runtime.quotes(
                    symbols,
                    provider=provider,
                    currentness=currentness,
                    max_age=max_age,
                    use_cache=use_cache,
                )
            except Exception as exc:
                envelope = to_error_envelope(exc)
                attempts.append(ProviderAttempt(provider, "failed", envelope.code))
                continue
            attempts.append(ProviderAttempt(provider, "ok"))
            return OrchestratedResult(
                result=self._mark_fallback(
                    result,
                    requested_provider=requested,
                    fallback=index > 0,
                ),
                attempts=tuple(attempts),
            )
        raise AllSourcesExhausted(
            "explicit Provider fallback policy exhausted",
            context={
                "providers": list(policy.providers),
                "errors": [f"{item.provider}:{item.code}" for item in attempts],
                "fallback": True,
            },
        )

    def bars(
        self,
        symbol: str,
        *,
        policy: FallbackPolicy,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> OrchestratedResult:
        attempts: list[ProviderAttempt] = []
        requested = policy.providers[0]
        for index, provider in enumerate(policy.providers):
            try:
                result = self.runtime.bars(
                    symbol,
                    provider=provider,
                    period=period,
                    count=count,
                    start=start,
                    adjustment=adjustment,
                    currentness=currentness,
                    max_age=max_age,
                    use_cache=use_cache,
                )
            except Exception as exc:
                envelope = to_error_envelope(exc)
                attempts.append(ProviderAttempt(provider, "failed", envelope.code))
                continue
            attempts.append(ProviderAttempt(provider, "ok"))
            return OrchestratedResult(
                result=self._mark_fallback(
                    result,
                    requested_provider=requested,
                    fallback=index > 0,
                ),
                attempts=tuple(attempts),
            )
        raise AllSourcesExhausted(
            "explicit Provider fallback policy exhausted",
            context={
                "providers": list(policy.providers),
                "errors": [f"{item.provider}:{item.code}" for item in attempts],
                "fallback": True,
            },
        )
