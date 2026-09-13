# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..providers import PROVIDERS, normalize_provider_id
from ..runtime import QueryRequest, QueryResponse, Runtime
from .response import ApiResponse, err, ok

_LEGACY_ROUTE_PROVIDER = {
    "tdx": "tdx",
    "local": "local_vipdoc",
}


class RuntimeFacadeAdapter:
    """Bridge facade-shaped calls into V14 orchestration.

    Legacy ``route='tdx'`` and ``route='local'`` map to canonical Provider ids.
    Legacy ``route='web'`` is intentionally ambiguous in the provider-first
    runtime and therefore requires an explicit ordered ``providers=...`` list.
    """

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    @staticmethod
    def _canonical_providers(providers: Sequence[str]) -> tuple[str, ...]:
        values: list[str] = []
        for provider in providers:
            provider_id = normalize_provider_id(provider)
            PROVIDERS.get(provider_id)
            if provider_id not in values:
                values.append(provider_id)
        if not values:
            raise ValueError("providers must not be empty")
        return tuple(values)

    def build_request(
        self,
        method: str,
        *args: Any,
        route: str | None = None,
        providers: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> QueryRequest:
        runtime_metadata = dict(metadata or {})
        normalized_route = str(route or "auto").strip().lower()
        canonical_providers = (
            self._canonical_providers(providers) if providers is not None else None
        )

        if normalized_route in _LEGACY_ROUTE_PROVIDER:
            runtime_metadata["provider"] = _LEGACY_ROUTE_PROVIDER[normalized_route]
        elif normalized_route == "web":
            if canonical_providers is None:
                raise ValueError(
                    "route='web' is ambiguous in the provider-first runtime; "
                    "pass providers=('eastmoney', 'tencent', ...) explicitly"
                )
            if any(pid in {"tdx", "local_vipdoc"} for pid in canonical_providers):
                raise ValueError("route='web' providers must be concrete web Provider ids")
        elif normalized_route != "auto":
            provider_id = normalize_provider_id(normalized_route)
            PROVIDERS.get(provider_id)
            runtime_metadata["provider"] = provider_id

        if canonical_providers is not None:
            runtime_metadata["providers"] = canonical_providers

        return QueryRequest(
            operation=method,
            params=dict(kwargs),
            metadata=runtime_metadata,
            args=tuple(args),
        )

    def execute(self, method: str, *args: Any, **kwargs: Any) -> QueryResponse:
        request = self.build_request(method, *args, **kwargs)
        return self.runtime.execute(request)

    def query(self, method: str, *args: Any, **kwargs: Any) -> ApiResponse:
        response = self.execute(method, *args, **kwargs)
        if response.success:
            return ok(response.data, code=response.code, extra=response.metadata)
        return err(
            response.error or "runtime request failed",
            code=response.code or "E9999",
            extra=response.metadata,
        )
