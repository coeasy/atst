# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v16 Gateway convergence — RuntimeGateway as a thin Client shell.

All business execution (Provider selection, planning, provenance) is owned by
:class:`tstdx.client_api.Client` on top of the zero-cache v13 kernel.  The
gateway only translates boundary calls into :class:`QueryResponse` envelopes.
The v14 :class:`Runtime` reference is kept exclusively for boundary-envelope
features: typed queries, batch dispatch, stream subscriptions and explicitly
registered compat handlers.

Design principles (locked by ``tests/v14/test_runtime_gateway.py``):

- No independent Provider selection or fallback — cross-Provider fallback only
  through ``FallbackPolicy`` executed by the Client's ProviderOrchestrator.
- No caching — every request hits the bound Provider directly.
- No provenance logic — ``QueryResult.meta`` carries canonical provenance; the
  gateway merely copies it into the response metadata.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..client_api import Client
    from ..orchestration import FallbackPolicy, OrchestratedResult
    from ..query import QuerySpec
    from ..result import QueryResult
    from .request import QueryRequest
    from .response import QueryResponse
    from .runtime import Runtime

_LEGACY_ROUTE_PROVIDER = {"tdx": "tdx", "local": "local_vipdoc"}


class RuntimeGateway:
    """Surface adapter delegating every capability to the canonical Client."""

    def __init__(
        self,
        runtime: Runtime | None = None,
        client: Client | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        from ..client_api import Client
        from .runtime import Runtime

        self.runtime: Runtime = runtime if runtime is not None else Runtime()
        self.client: Client = client if client is not None else Client(**runtime_kwargs)

    def close(self) -> None:
        self.client.close()

    # -- Response envelope helpers ------------------------------------------ #

    @staticmethod
    def _provider_from_route(route: str | None) -> str | None:
        if route is None:
            return None
        normalized = str(route).strip().lower()
        if normalized in {"", "auto"}:
            return None
        return _LEGACY_ROUTE_PROVIDER.get(normalized, normalized)

    @staticmethod
    def _provider_selection(
        provider: str | None,
        route: str | None,
        providers: Sequence[str] | None,
    ) -> tuple[str | None, FallbackPolicy | None]:
        from ..orchestration import FallbackPolicy

        if provider is not None:
            return provider, None
        routed = RuntimeGateway._provider_from_route(route)
        if routed is not None:
            return routed, None
        if providers is None:
            return None, None
        order = tuple(str(item) for item in providers)
        if not order:
            return None, None
        if len(order) == 1:
            return order[0], None
        return None, FallbackPolicy.build(*order)

    def _wrap(self, result: Any, *, operation: str) -> QueryResponse:
        from .response import QueryResponse

        attempts: list[dict[str, Any]] | None = None
        if hasattr(result, "attempts") and hasattr(result, "result"):
            attempts = [
                {"provider": a.provider, "status": a.status, "code": a.code}
                for a in result.attempts
            ]
            result = result.result
        provenance = result.meta.provenance
        metadata: dict[str, Any] = {
            "operation": operation,
            "execution": "client-kernel",
            "provider": provenance.provider,
            "channel": result.meta.channel,
            "query_fingerprint": result.meta.fingerprint,
            "provenance": {
                "provider": provenance.provider,
                "channel": provenance.channel,
                "capability": provenance.capability,
                "kind": provenance.kind.value,
                "observed_at_ns": provenance.observed_at_ns,
                "provider_timestamp": provenance.provider_timestamp,
                "cache_tier": provenance.cache_tier,
                "requested_provider": provenance.requested_provider,
                "fallback": provenance.fallback,
            },
        }
        if attempts is not None:
            metadata["provider_attempts"] = attempts
        return QueryResponse.ok(result.data, **metadata)

    def _execute_call(self, operation: str, call: Callable[[], Any]) -> QueryResponse:
        from .response import QueryResponse

        try:
            result = call()
        except Exception as exc:
            return QueryResponse.fail(
                str(exc),
                code=str(getattr(exc, "code", "") or ""),
                operation=operation,
                error_type=type(exc).__name__,
            )
        return self._wrap(result, operation=operation)

    # -- K 线 / 行情 ------------------------------------------------------ #

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
        max_age: float | None = None,
        route: str | None = None,
        provider: str | None = None,
        providers: Sequence[str] | None = None,
    ) -> QueryResponse:
        """Fetch K-line / minute bars through the Client kernel."""
        selected, policy = self._provider_selection(provider, route, providers)
        return self._execute_call(
            "bars",
            lambda: self.client.bars(
                symbol,
                provider=selected,
                policy=policy,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness=currentness,
                max_age=max_age,
            ),
        )

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        currentness: str = "live",
        max_age: float | None = None,
        route: str | None = None,
        provider: str | None = None,
        providers: Sequence[str] | None = None,
    ) -> QueryResponse:
        """Fetch real-time quote snapshots through the Client kernel."""
        selected, policy = self._provider_selection(provider, route, providers)
        return self._execute_call(
            "quotes",
            lambda: self.client.quotes(
                symbols,
                provider=selected,
                policy=policy,
                currentness=currentness,
                max_age=max_age,
            ),
        )

    def snapshot(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return self._execute_call("snapshot", lambda: self.client.snapshot(symbol, **kwargs))

    def minute(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return self._execute_call("minute", lambda: self.client.minute(symbol, **kwargs))

    def trades(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return self._execute_call("trades", lambda: self.client.trades(symbol, **kwargs))

    # -- 元数据 / 通用 ---------------------------------------------------- #

    def security_count(self, market: int | str = 0, **kwargs: Any) -> QueryResponse:
        return self._execute_call(
            "security_count", lambda: self.client.security_count(market=market, **kwargs)
        )

    def security_list(
        self, market: int | str = 0, start: int = 0, **kwargs: Any
    ) -> QueryResponse:
        return self._execute_call(
            "security_list",
            lambda: self.client.security_list(market=market, start=start, **kwargs),
        )

    def finance_info(self, symbol: str, **kwargs: Any) -> QueryResponse:
        """Surface alias for the canonical ``finance`` capability."""
        return self._execute_call(
            "finance_info", lambda: self.client.call("finance", symbol, **kwargs)
        )

    def minute_today(self, symbol: str, **kwargs: Any) -> QueryResponse:
        """Surface alias for the canonical ``minute`` capability."""
        return self._execute_call(
            "minute_today", lambda: self.client.call("minute", symbol, **kwargs)
        )

    # -- 批量 / 直接执行（v14 边界信封） ----------------------------------- #

    def execute_batch(
        self,
        requests: Sequence[QueryRequest],
        *,
        max_concurrent: int = 8,
    ) -> list[QueryResponse]:
        """Execute multiple envelope requests via the v14 orchestration shell."""
        return self.runtime.execute_batch(requests, max_concurrent=max_concurrent)

    def execute(self, request: QueryRequest) -> QueryResponse:
        """Execute a pre-built QueryRequest via the v14 orchestration shell."""
        return self.runtime.execute(request)

    def execute_typed(self, query: Any, **kwargs: Any) -> QueryResponse:
        """Execute a typed CapabilityQuery via the v14 orchestration shell."""
        return self.runtime.execute_typed(query, **kwargs)

    # -- 流订阅 ----------------------------------------------------------- #

    def subscribe(
        self,
        symbols: str | tuple[str, ...],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        subscription_id: str | None = None,
    ) -> Any:
        """Subscribe to a real-time quote stream via Runtime."""
        return self.runtime.subscribe(
            "quotes",
            symbols,
            provider=provider,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            subscription_id=subscription_id,
        )

    # -- 诊断 ------------------------------------------------------------- #

    @property
    def providers(self) -> list[str]:
        """Return registered Provider names on the orchestration shell."""
        return list(self.runtime.router.names())

    def subscriptions(self) -> Mapping[str, Any]:
        """Return active stream subscriptions."""
        return self.runtime.subscriptions()

    # -- Capability / policy dispatch (直通 Client) -------------------------- #

    def call(
        self,
        capability: str,
        *args: Any,
        **kwargs: Any,
    ) -> QueryResult[Any] | OrchestratedResult:
        """Dispatch any core or migrated capability through the Client."""
        return self.client.call(capability, *args, **kwargs)

    def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        """Execute with explicit cross-Provider fallback policy via Client."""
        return self.client.execute_with_policy(spec, policy=policy)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        from ..capability_catalog import is_migrated_capability

        if is_migrated_capability(name):
            return getattr(self.client, name)
        raise AttributeError(name)


class RuntimeAsyncClient:
    """Async facade over RuntimeGateway — delegates via asyncio.to_thread."""

    def __init__(self, gateway: RuntimeGateway | None = None) -> None:
        self.gateway = gateway or RuntimeGateway()

    async def close(self) -> None:
        await asyncio.to_thread(self.gateway.close)

    async def __aenter__(self) -> RuntimeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def execute(self, request: QueryRequest) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.execute, request)

    async def execute_typed(self, query: Any, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.execute_typed, query, **kwargs)

    async def execute_batch(
        self,
        requests: Sequence[QueryRequest],
        *,
        max_concurrent: int = 8,
    ) -> list[QueryResponse]:
        return await asyncio.to_thread(
            self.gateway.execute_batch,
            requests,
            max_concurrent=max_concurrent,
        )

    async def call(
        self,
        capability: str,
        *args: Any,
        **kwargs: Any,
    ) -> QueryResult[Any] | OrchestratedResult:
        return await asyncio.to_thread(
            self.gateway.call,
            capability,
            *args,
            **kwargs,
        )

    async def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        return await asyncio.to_thread(
            self.gateway.execute_with_policy,
            spec,
            policy=policy,
        )

    async def bars(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.bars, symbol, **kwargs)

    async def quotes(self, symbols: str | Sequence[str], **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.quotes, symbols, **kwargs)

    async def snapshot(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.snapshot, symbol, **kwargs)

    async def minute(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.minute, symbol, **kwargs)

    async def trades(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.trades, symbol, **kwargs)

    async def security_count(self, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.security_count, **kwargs)

    async def security_list(self, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.security_list, **kwargs)

    async def minute_today(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.minute_today, symbol, **kwargs)

    async def finance_info(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.finance_info, symbol, **kwargs)
