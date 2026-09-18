# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..errors import ValidationError
from ..provider.router import ProviderAttempts, ProviderRouter
from ..providers import PROVIDERS, normalize_provider_id
from ..query import QueryPlan, QueryPlanner, QuerySpec
from ..result import Provenance, QueryResult

_CORE_CAPABILITIES = frozenset({"quotes", "bars"})


class SemanticExecutionAdapter:
    """Translate V14 orchestration requests into canonical v13 query contracts.

    The adapter is a pure translator plus a single-Provider executor seam: it
    performs no caching, never switches Providers, and delegates execution to
    exactly one bound Provider per compiled :class:`QueryPlan`.  Cross-Provider
    fallback belongs only to the explicit ``FallbackPolicy`` path in
    :class:`tstdx.orchestration.ProviderOrchestrator`.
    """

    def __init__(
        self,
        *,
        query_planner: QueryPlanner | None = None,
    ) -> None:
        self.query_planner = query_planner or QueryPlanner()

    @staticmethod
    def _known_provider(provider: str) -> bool:
        try:
            PROVIDERS.get(provider)
        except ValidationError:
            return False
        return True

    def can_execute(self, request: Any, providers: Sequence[str]) -> bool:
        operation = str(getattr(request, "operation", "") or "").strip().lower()
        if (
            not operation
            or not providers
            or not all(self._known_provider(item) for item in providers)
        ):
            return False
        if operation in _CORE_CAPABILITIES:
            return True
        return any(PROVIDERS.supports(provider, operation) for provider in providers)

    @staticmethod
    def _symbols(request: Any, capability: str) -> str | Sequence[str]:
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        if args:
            return args[0]
        if capability == "bars":
            if "symbol" in params:
                return params["symbol"]
            if "symbols" in params:
                return params["symbols"]
        else:
            if "symbols" in params:
                return params["symbols"]
            if "symbol" in params:
                return params["symbol"]
        return ()

    @staticmethod
    def _deadline_ms(request: Any) -> int:
        metadata = dict(getattr(request, "metadata", {}))
        if metadata.get("deadline_ms") is not None:
            return int(metadata["deadline_ms"])
        if metadata.get("timeout") is not None:
            return max(1, int(float(metadata["timeout"]) * 1000))
        return 5000

    @classmethod
    def _build_spec(cls, request: Any, provider: str) -> QuerySpec:
        capability = str(getattr(request, "operation", "") or "").strip().lower()
        params = dict(getattr(request, "params", {}))
        metadata = dict(getattr(request, "metadata", {}))
        args = tuple(getattr(request, "args", ()))

        semantic_keys = {
            "symbol",
            "symbols",
            "period",
            "count",
            "start",
            "adjust",
            "adjustment",
            "allow_partial",
        }
        options = {key: value for key, value in params.items() if key not in semantic_keys}
        if len(args) > 1:
            options["runtime_args_tail"] = list(args[1:])

        adjustment = params.get("adjustment", params.get("adjust", ""))
        return QuerySpec.build(
            capability,
            symbols=cls._symbols(request, capability),
            provider=provider,
            channel=metadata.get("channel"),
            period=str(params.get("period", "day" if capability == "bars" else "")),
            count=int(params.get("count", 320 if capability == "bars" else 0)),
            start=int(params.get("start", 0)),
            adjustment=str(adjustment or ""),
            allow_partial=bool(params.get("allow_partial", False)),
            currentness=metadata.get("currentness", "auto"),
            max_age=metadata.get("max_age"),
            deadline_ms=cls._deadline_ms(request),
            options=options,
        )

    def compile(self, request: Any, provider: str) -> QueryPlan:
        canonical = normalize_provider_id(provider)
        return self.query_planner.compile(self._build_spec(request, canonical))

    @staticmethod
    def _fallback_provenance(
        provenance: Provenance,
        *,
        requested_provider: str | None,
        selected_provider: str,
    ) -> Provenance:
        if not requested_provider or requested_provider == selected_provider:
            return provenance
        return replace(
            provenance,
            requested_provider=requested_provider,
            fallback=True,
        )

    @staticmethod
    def _policy_candidates(
        request: Any,
        candidates: tuple[str, ...],
        *,
        requested_provider: str | None,
    ) -> tuple[str, ...]:
        """Statically drop providers the canonical registry can never serve.

        This is deterministic registry routing, not runtime fallback: exactly
        one candidate survives and is executed.  Caller-specified provider
        order is never rewritten.
        """
        if requested_provider is not None:
            return candidates
        operation = str(getattr(request, "operation", "") or "").strip().lower()
        supported: list[str] = []
        for provider in candidates:
            try:
                if PROVIDERS.supports(provider, operation):
                    supported.append(provider)
            except ValidationError:
                continue
        return tuple(supported) or candidates[:1]

    def execute(
        self,
        *,
        request: Any,
        router: ProviderRouter,
        providers: Sequence[str],
        attempts: ProviderAttempts,
        requested_provider: str | None = None,
    ) -> tuple[str, QueryResult[Any]]:
        """Execute against exactly one Provider.

        The adapter never iterates candidates looking for a working Provider:
        a failing Provider raises.  Cross-Provider fallback requires an
        explicit ``FallbackPolicy`` through the orchestrator.
        """
        candidates = tuple(normalize_provider_id(item) for item in providers)
        if not candidates:
            raise RuntimeError("no providers are available for semantic execution")
        requested = (
            normalize_provider_id(requested_provider) if requested_provider else None
        )
        provider = self._policy_candidates(
            request,
            candidates,
            requested_provider=requested,
        )[0]
        plan = self.compile(request, provider)
        before = len(attempts)
        raw = router.query(provider, request, attempts=attempts)
        if len(attempts) == before:
            attempts.append({"provider": provider, "status": "selected"})
        provenance = self._fallback_provenance(
            Provenance.direct(plan),
            requested_provider=requested,
            selected_provider=provider,
        )
        result = QueryResult.from_plan(raw, plan=plan, provenance=provenance)
        return provider, result
