# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Phase 6 Gateway convergence — RuntimeGateway.

Bridges CLI / surface commands to v14 Runtime execution without
implementing independent Provider selection, fallback, cache, or
provenance logic. All routing, caching, and execution policy live in
Runtime; the gateway only translates boundary calls.

Usage::

    gateway = RuntimeGateway(runtime)
    result = gateway.bars("sh600519", count=30)
    if result.success:
        print(result.data)

Design principles:

- No duplicate Provider selection — delegated to RuntimeFacadeAdapter.
- No caching — every request hits the bound Provider directly (zero-cache kernel).
- No provenance logic — QueryResult meta carries canonical provenance.
- No capability dispatch — operation names map to Registry capabilities.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..orchestration import ProviderOrchestrator

if TYPE_CHECKING:
    from ..orchestration import FallbackPolicy, OrchestratedResult
    from ..query import QuerySpec
    from ..result import QueryResult
    from .legacy_bridge import LegacyRuntimeBridge
    from .request import QueryRequest
    from .response import QueryResponse
    from .runtime import Runtime


class RuntimeGateway:
    """Thin surface adapter over v14 Runtime for CLI / HTTP / WS callers.

    Every public method returns a :class:`QueryResponse` with canonical
    provenance metadata. Callers must not inspect ``data`` for routing
    information — the response ``metadata`` carries ``provider``,
    ``channel``, ``query_fingerprint``, and ``provenance``.
    """

    def __init__(
        self,
        runtime: Runtime | None = None,
        bridge: LegacyRuntimeBridge | None = None,
    ) -> None:
        from ..facade.runtime_adapter import RuntimeFacadeAdapter
        from .runtime import Runtime

        if runtime is None:
            runtime = Runtime()
        self.runtime = runtime
        self._adapter = RuntimeFacadeAdapter(self.runtime)
        self._bridge: LegacyRuntimeBridge | None = bridge or getattr(runtime, "_bridge", None)
        self._orchestrator: ProviderOrchestrator | None = (
            ProviderOrchestrator(self._bridge.runtime) if self._bridge is not None else None
        )

    # -- K 线 / 行情 ------------------------------------------------------ #

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: str = "dict",
        strict: bool = False,
        route: str | None = None,
        providers: Sequence[str] | None = None,
    ) -> QueryResponse:
        """Fetch K-line / minute bars via Runtime execution."""
        params: dict[str, Any] = {
            "period": period,
            "count": count,
            "start": start,
            "as_format": as_format,
            "strict": strict,
            "index": index,
        }
        if market is not None:
            params["market"] = market
        return self._adapter.execute(
            "bars",
            symbol,
            route=route,
            providers=providers,
            **params,
        )

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: str = "dict",
        route: str | None = None,
        providers: Sequence[str] | None = None,
    ) -> QueryResponse:
        """Fetch real-time quote snapshots via Runtime execution."""
        return self._adapter.execute(
            "quotes",
            symbols,
            route=route,
            providers=providers,
            as_format=as_format,
        )

    # -- 元数据 / 通用 ---------------------------------------------------- #

    def security_count(self, market: int | str = 0, **kwargs: Any) -> QueryResponse:
        """Query security count for a market via Runtime."""
        return self._adapter.execute("security_count", market, **kwargs)

    def finance_info(self, symbol: str, **kwargs: Any) -> QueryResponse:
        """Query finance info via Runtime."""
        return self._adapter.execute("finance_info", symbol, **kwargs)

    def minute_today(self, symbol: str, **kwargs: Any) -> QueryResponse:
        """Query today's minute data via Runtime."""
        return self._adapter.execute("minute_today", symbol, **kwargs)

    def security_list(
        self, market: int | str = 0, start: int = 0, **kwargs: Any
    ) -> QueryResponse:
        """Query security list via Runtime."""
        return self._adapter.execute("security_list", market, start=start, **kwargs)

    # -- 批量执行 --------------------------------------------------------- #

    def execute_batch(
        self,
        requests: Sequence[QueryRequest],
        *,
        max_concurrent: int = 8,
    ) -> list[QueryResponse]:
        """Execute multiple requests directly against their bound Providers."""
        return self.runtime.execute_batch(requests, max_concurrent=max_concurrent)

    # -- 直接执行 --------------------------------------------------------- #

    def execute(self, request: QueryRequest) -> QueryResponse:
        """Execute a pre-built QueryRequest via Runtime."""
        return self.runtime.execute(request)

    def execute_typed(self, query: Any, **kwargs: Any) -> QueryResponse:
        """Execute a typed CapabilityQuery via Runtime."""
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
        """Return registered Provider names."""
        return list(self.runtime.router.names())

    def subscriptions(self) -> Mapping[str, Any]:
        """Return active stream subscriptions."""
        return self.runtime.subscriptions()

    # -- Policy / capability dispatch -------------------------------------- #

    def call(
        self,
        capability: str,
        *args: Any,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """Dispatch a capability through the v13 UnifiedRuntime bridge."""
        if self._bridge is None:
            from ..runtime_v13 import UnifiedRuntime

            self._bridge = LegacyRuntimeBridge(UnifiedRuntime())
        return self._bridge.call(
            capability,
            *args,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
            **kwargs,
        )

    def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        """Execute with explicit cross-Provider fallback policy."""
        if self._orchestrator is None:
            if self._bridge is None:
                from ..runtime_v13 import UnifiedRuntime

                self._bridge = LegacyRuntimeBridge(UnifiedRuntime())
            self._orchestrator = ProviderOrchestrator(self._bridge.runtime)
        return self._orchestrator.execute(spec, policy=policy)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        from ..capability_catalog import is_migrated_capability

        if is_migrated_capability(name):
            def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                provider = kwargs.pop("provider", None)
                kwargs.pop("channel", None)
                currentness = kwargs.pop("currentness", "business")
                max_age = kwargs.pop("max_age", None)
                if self._bridge is None:
                    from ..runtime_v13 import UnifiedRuntime

                    self._bridge = LegacyRuntimeBridge(UnifiedRuntime())
                return self._bridge.call(
                    name,
                    *args,
                    provider=provider,
                    currentness=currentness,
                    max_age=max_age,
                    **kwargs,
                )

            migrated.__name__ = name
            migrated.__qualname__ = f"RuntimeGateway.{name}"
            return migrated
        raise AttributeError(name)


class RuntimeAsyncClient:
    """Async facade over RuntimeGateway — delegates via asyncio.to_thread."""

    def __init__(self, gateway: RuntimeGateway | None = None) -> None:
        self.gateway = gateway or RuntimeGateway()

    async def close(self) -> None:
        bridge = self.gateway._bridge
        if bridge is not None:
            await asyncio.to_thread(bridge.close)

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
    ) -> QueryResult[Any]:
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

    async def snapshot(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.gateway._bridge.snapshot, symbol, **kwargs)

    async def minute(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.gateway._bridge.minute, symbol, **kwargs)

    async def trades(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.gateway._bridge.trades, symbol, **kwargs)

    async def security_count(self, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.security_count, **kwargs)

    async def security_list(self, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.security_list, **kwargs)

    async def minute_today(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.minute_today, symbol, **kwargs)

    async def finance_info(self, symbol: str, **kwargs: Any) -> QueryResponse:
        return await asyncio.to_thread(self.gateway.finance_info, symbol, **kwargs)
