# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 public Client API.

This is the only high-level business API. It is intentionally thin: request
methods construct canonical QuerySpec/StreamSpec values and delegate to the
single Provider runtime or explicit ProviderOrchestrator. It contains no source
routing, fallback heuristics or protocol-specific business logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .batch import BatchResult
from .orchestration import FallbackPolicy, OrchestratedResult, ProviderOrchestrator
from .query import QuerySpec
from .result import QueryResult
from .runtime import UnifiedRuntime
from .stream_contract import StreamPlanner, StreamSpec
from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream

__all__ = ["Client", "AsyncClient"]


class Client:
    """Canonical synchronous v13 client."""

    def __init__(self, runtime: UnifiedRuntime | None = None, **runtime_kwargs: Any) -> None:
        if runtime is not None and runtime_kwargs:
            raise ValueError("runtime and runtime kwargs are mutually exclusive")
        self.runtime = runtime or UnifiedRuntime(**runtime_kwargs)
        self.orchestrator = ProviderOrchestrator(self.runtime)
        self.stream_planner = StreamPlanner()
        self._owns_runtime = runtime is None

    def close(self) -> None:
        if self._owns_runtime:
            self.runtime.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def execute(self, spec: QuerySpec, *, use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.execute(spec, use_cache=use_cache)

    def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
        use_cache: bool = True,
    ) -> OrchestratedResult:
        return self.orchestrator.execute(spec, policy=policy, use_cache=use_cache)

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        policy: FallbackPolicy | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any] | OrchestratedResult:
        spec = QuerySpec.build(
            "quotes",
            symbols=symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
        )
        if policy is not None:
            if provider is not None:
                raise ValueError("provider and fallback policy are mutually exclusive")
            return self.execute_with_policy(spec, policy=policy, use_cache=use_cache)
        return self.execute(spec, use_cache=use_cache)

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> BatchResult[QueryResult[Any]]:
        return self.runtime.quotes_batch(
            symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
            use_cache=use_cache,
        )

    def bars(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        policy: FallbackPolicy | None = None,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> QueryResult[Any] | OrchestratedResult:
        spec = QuerySpec.build(
            "bars",
            symbols=symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=currentness,
            max_age=max_age,
        )
        if policy is not None:
            if provider is not None:
                raise ValueError("provider and fallback policy are mutually exclusive")
            return self.execute_with_policy(spec, policy=policy, use_cache=use_cache)
        return self.execute(spec, use_cache=use_cache)

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.runtime.snapshot(symbol, provider=provider, use_cache=use_cache)

    def minute(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.runtime.minute(symbol, provider=provider, use_cache=use_cache)

    def trades(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
        start: int = 0,
        count: int = 0,
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.runtime.trades(
            symbol,
            provider=provider,
            start=start,
            count=count,
            use_cache=use_cache,
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str = "tdx",
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.runtime.security_count(
            market=market,
            provider=provider,
            use_cache=use_cache,
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str = "tdx",
        use_cache: bool = True,
    ) -> QueryResult[Any]:
        return self.runtime.security_list(
            market=market,
            start=start,
            provider=provider,
            use_cache=use_cache,
        )

    def stream(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: Any | None = None,
        on_error: Any | None = None,
    ) -> StatefulQuoteStream:
        plan = self.stream_planner.compile(
            StreamSpec.build(
                symbols,
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
            )
        )
        stream = StatefulQuoteStream(runtime=self.runtime, provider=plan.provider)
        stream.subscribe(
            plan.symbols,
            interval=plan.interval,
            diff_only=plan.diff_only,
            max_queue=plan.max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        return stream


class AsyncClient:
    """Async facade over the same v13 semantic/runtime contracts.

    Blocking provider work is delegated to a worker thread until provider-native
    async adapters are promoted. This preserves one semantic core instead of
    maintaining a second async architecture.
    """

    def __init__(self, client: Client | None = None, **runtime_kwargs: Any) -> None:
        if client is not None and runtime_kwargs:
            raise ValueError("client and runtime kwargs are mutually exclusive")
        self.client = client or Client(**runtime_kwargs)
        self._owns_client = client is None
        self.stream_planner = StreamPlanner()

    async def close(self) -> None:
        if self._owns_client:
            await asyncio.to_thread(self.client.close)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def execute(self, spec: QuerySpec, *, use_cache: bool = True) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.execute, spec, use_cache=use_cache)

    async def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
        use_cache: bool = True,
    ) -> OrchestratedResult:
        return await asyncio.to_thread(
            self.client.execute_with_policy,
            spec,
            policy=policy,
            use_cache=use_cache,
        )

    async def quotes(self, symbols: str | Sequence[str], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.quotes, symbols, **kwargs)

    async def quotes_batch(self, symbols: Sequence[str], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.quotes_batch, symbols, **kwargs)

    async def bars(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.bars, symbol, **kwargs)

    async def snapshot(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.snapshot, symbol, **kwargs)

    async def minute(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.minute, symbol, **kwargs)

    async def trades(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.trades, symbol, **kwargs)

    async def security_count(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.security_count, **kwargs)

    async def security_list(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.security_list, **kwargs)

    def stream(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: Any | None = None,
        on_error: Any | None = None,
    ) -> AsyncStatefulQuoteStream:
        plan = self.stream_planner.compile(
            StreamSpec.build(
                symbols,
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
            )
        )
        stream = AsyncStatefulQuoteStream(
            runtime=self.client.runtime,
            provider=plan.provider,
        )
        stream.subscribe(
            plan.symbols,
            interval=plan.interval,
            diff_only=plan.diff_only,
            max_queue=plan.max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        return stream
