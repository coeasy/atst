# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 public Client API: the sole high-level business surface."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .batch import BatchResult
from .capability_catalog import (
    MIGRATED_CAPABILITIES,
    default_provider_for,
    is_migrated_capability,
)
from .errors import ValidationError
from .orchestration import FallbackPolicy, OrchestratedResult, ProviderOrchestrator
from .query import QuerySpec
from .result import QueryResult
from .runtime import UnifiedRuntime
from .stream_contract import StreamPlanner, StreamSpec
from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream

__all__ = ["Client", "AsyncClient"]


class Client:
    """Canonical synchronous v13 client.

    Retired UnifiedQuoteAPI abilities are exposed through :meth:`call` and as
    same-name dynamic methods.  They still compile into QuerySpec and execute
    through the single UnifiedRuntime; no facade/router compatibility path is
    reintroduced.
    """

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

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return tuple(sorted(MIGRATED_CAPABILITIES))

    def execute(self, spec: QuerySpec, *, use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.execute(spec, use_cache=use_cache)

    def call(
        self,
        capability: str,
        *args: Any,
        provider: str | None = None,
        channel: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """Execute any migrated business capability through the canonical runtime."""
        cap = str(capability).strip().lower()
        if not is_migrated_capability(cap):
            raise ValidationError(
                f"未知 migrated capability {capability!r}",
                context={"capability": cap},
            )
        selected = provider or default_provider_for(cap)
        spec = QuerySpec.build(
            cap,
            provider=selected,
            channel=channel,
            currentness=currentness,
            max_age=max_age,
            options={"args": list(args), "kwargs": kwargs},
        )
        # Stateful/side-effecting sync is never cacheable even if a caller asks.
        if cap == "sync_daily":
            use_cache = False
        return self.execute(spec, use_cache=use_cache)

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):
            def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                provider = kwargs.pop("provider", None)
                channel = kwargs.pop("channel", None)
                currentness = kwargs.pop("currentness", "business")
                max_age = kwargs.pop("max_age", None)
                use_cache = bool(kwargs.pop("use_cache", True))
                return self.call(
                    name,
                    *args,
                    provider=provider,
                    channel=channel,
                    currentness=currentness,
                    max_age=max_age,
                    use_cache=use_cache,
                    **kwargs,
                )
            migrated.__name__ = name
            migrated.__qualname__ = f"Client.{name}"
            return migrated
        raise AttributeError(name)

    def execute_with_policy(self, spec: QuerySpec, *, policy: FallbackPolicy, use_cache: bool = True) -> OrchestratedResult:
        return self.orchestrator.execute(spec, policy=policy, use_cache=use_cache)

    def quotes(self, symbols: str | Sequence[str], *, provider: str | None = None, policy: FallbackPolicy | None = None, currentness: str = "live", max_age: float | None = None, use_cache: bool = True) -> QueryResult[Any] | OrchestratedResult:
        spec = QuerySpec.build("quotes", symbols=symbols, provider=provider, currentness=currentness, max_age=max_age)
        if policy is not None:
            if provider is not None:
                raise ValueError("provider and fallback policy are mutually exclusive")
            return self.execute_with_policy(spec, policy=policy, use_cache=use_cache)
        return self.execute(spec, use_cache=use_cache)

    def quotes_batch(self, symbols: Sequence[str], *, provider: str | None = None, currentness: str = "live", max_age: float | None = None, use_cache: bool = True) -> BatchResult[QueryResult[Any]]:
        return self.runtime.quotes_batch(symbols, provider=provider, currentness=currentness, max_age=max_age, use_cache=use_cache)

    def bars(self, symbol: str, *, provider: str | None = None, policy: FallbackPolicy | None = None, period: str = "day", count: int = 320, start: int = 0, adjustment: str = "", currentness: str = "historical", max_age: float | None = None, use_cache: bool = True) -> QueryResult[Any] | OrchestratedResult:
        spec = QuerySpec.build("bars", symbols=symbol, provider=provider, period=period, count=count, start=start, adjustment=adjustment, currentness=currentness, max_age=max_age)
        if policy is not None:
            if provider is not None:
                raise ValueError("provider and fallback policy are mutually exclusive")
            return self.execute_with_policy(spec, policy=policy, use_cache=use_cache)
        return self.execute(spec, use_cache=use_cache)

    def snapshot(self, symbol: str, *, provider: str = "tdx", use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.snapshot(symbol, provider=provider, use_cache=use_cache)

    def minute(self, symbol: str, *, provider: str = "tdx", use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.minute(symbol, provider=provider, use_cache=use_cache)

    def trades(self, symbol: str, *, provider: str = "tdx", start: int = 0, count: int = 0, use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.trades(symbol, provider=provider, start=start, count=count, use_cache=use_cache)

    def security_count(self, *, market: int | str = 0, provider: str = "tdx", use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.security_count(market=market, provider=provider, use_cache=use_cache)

    def security_list(self, *, market: int | str = 0, start: int = 0, provider: str = "tdx", use_cache: bool = True) -> QueryResult[Any]:
        return self.runtime.security_list(market=market, start=start, provider=provider, use_cache=use_cache)

    def stream(self, symbols: str | Sequence[str], *, provider: str = "tdx", interval: float = 1.0, diff_only: bool = False, max_queue: int = 1024, on_quote: Any | None = None, on_error: Any | None = None) -> StatefulQuoteStream:
        plan = self.stream_planner.compile(StreamSpec.build(symbols, provider=provider, interval=interval, diff_only=diff_only, max_queue=max_queue))
        stream = StatefulQuoteStream(runtime=self.runtime, provider=plan.provider)
        stream.subscribe(plan.symbols, interval=plan.interval, diff_only=plan.diff_only, max_queue=plan.max_queue, on_quote=on_quote, on_error=on_error)
        return stream


class AsyncClient:
    """Async facade over the exact same v13 semantic/runtime contracts."""

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

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return Client.capabilities()

    async def execute(self, spec: QuerySpec, *, use_cache: bool = True) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.execute, spec, use_cache=use_cache)

    async def call(self, capability: str, *args: Any, **kwargs: Any) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.call, capability, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):
            async def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                return await asyncio.to_thread(getattr(self.client, name), *args, **kwargs)
            migrated.__name__ = name
            migrated.__qualname__ = f"AsyncClient.{name}"
            return migrated
        raise AttributeError(name)

    async def execute_with_policy(self, spec: QuerySpec, *, policy: FallbackPolicy, use_cache: bool = True) -> OrchestratedResult:
        return await asyncio.to_thread(self.client.execute_with_policy, spec, policy=policy, use_cache=use_cache)

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

    def stream(self, symbols: str | Sequence[str], *, provider: str = "tdx", interval: float = 1.0, diff_only: bool = False, max_queue: int = 1024, on_quote: Any | None = None, on_error: Any | None = None) -> AsyncStatefulQuoteStream:
        plan = self.stream_planner.compile(StreamSpec.build(symbols, provider=provider, interval=interval, diff_only=diff_only, max_queue=max_queue))
        stream = AsyncStatefulQuoteStream(runtime=self.client.runtime, provider=plan.provider)
        stream.subscribe(plan.symbols, interval=plan.interval, diff_only=plan.diff_only, max_queue=plan.max_queue, on_quote=on_quote, on_error=on_error)
        return stream
