from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..runtime import QueryRequest, QueryResponse, Runtime
from .response import ApiResponse, err, ok

_ROUTE_PROVIDERS = {"tdx", "web", "local", "cache"}


class RuntimeFacadeAdapter:
    """Bridge existing facade-shaped calls into the v14 runtime.

    The adapter keeps public method arguments intact while translating facade
    routing hints into runtime provider metadata. It is deliberately separate
    from ``UnifiedQuoteAPI`` so migration can be contract-tested before the
    legacy router is replaced.
    """

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

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
        if route and route != "auto":
            if route not in _ROUTE_PROVIDERS:
                raise ValueError(f"unsupported runtime route: {route}")
            runtime_metadata["provider"] = route
        if providers is not None:
            runtime_metadata["providers"] = tuple(providers)
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
