# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Explicit cross-Provider orchestration above the strict runtime kernel."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .error_envelope import to_error_envelope
from .errors import AllSourcesExhausted
from .providers import PROVIDERS, resolve_provider
from .query import QuerySpec
from .result import QueryResult
from .runtime_v13 import UnifiedRuntime

__all__ = [
    "FallbackPolicy",
    "ProviderAttempt",
    "OrchestratedResult",
    "ProviderOrchestrator",
]


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    """Explicit ordered Provider fallback policy."""

    providers: tuple[str, ...]

    @classmethod
    def build(cls, *providers: str) -> FallbackPolicy:
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
    """Only cross-Provider execution implementation in the v13 architecture."""

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
        return QueryResult(data=result.data, meta=replace(result.meta, provenance=provenance))

    def execute(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
        use_cache: bool = True,
    ) -> OrchestratedResult:
        """Execute one semantic QuerySpec across an explicit Provider order.

        The base QuerySpec's provider, if any, is replaced by each policy
        Provider in turn. All other semantics remain identical, preserving a
        meaningful per-attempt comparison and fingerprint.
        """

        attempts: list[ProviderAttempt] = []
        requested = policy.providers[0]
        base = replace(spec, provider=None)
        for index, provider in enumerate(policy.providers):
            attempt_spec = replace(base, provider=provider)
            try:
                result = self.runtime.execute(attempt_spec, use_cache=use_cache)
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
                "capability": spec.capability,
            },
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
        return self.execute(
            QuerySpec.build(
                "quotes",
                symbols=symbols,
                currentness=currentness,
                max_age=max_age,
            ),
            policy=policy,
            use_cache=use_cache,
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
        return self.execute(
            QuerySpec.build(
                "bars",
                symbols=symbol,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness=currentness,
                max_age=max_age,
            ),
            policy=policy,
            use_cache=use_cache,
        )
