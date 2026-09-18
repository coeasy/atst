# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 public Client API: the sole high-level business surface."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
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
from .runtime.kernel import UnifiedRuntime
from .stream_contract import StreamPlanner, StreamSpec
from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream

__all__ = ["Client", "AsyncClient"]

_CORE_CAPABILITIES = frozenset(
    {"quotes", "bars", "snapshot", "minute", "trades", "security_count", "security_list"}
)


def _json_contract(value: Any) -> Any:
    """Convert deterministic domain values into QuerySpec-safe JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_contract(value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_contract(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_contract(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_contract(item) for item in value]
    raise ValidationError(
        "capability 参数必须可转换为确定性 JSON contract",
        context={"value_type": type(value).__name__},
    )


class Client:
    """Canonical synchronous v13 client.

    Tier-A uses strongly typed QuerySpec fields. Retired-facade abilities use the
    migrated capability catalog, but still execute through the same QueryPlanner
    and UnifiedRuntime. No facade/router compatibility kernel is reintroduced.
    """

    def __init__(
        self,
        runtime: UnifiedRuntime | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        if runtime is not None and runtime_kwargs:
            raise ValueError("runtime and runtime kwargs are mutually exclusive")
        self.runtime = runtime or UnifiedRuntime(**runtime_kwargs)
        self.orchestrator = ProviderOrchestrator(self.runtime)
        self.stream_planner = StreamPlanner()
        self._owns_runtime = runtime is None

    def close(self) -> None:
        if self._owns_runtime:
            self.runtime.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return tuple(sorted(_CORE_CAPABILITIES | MIGRATED_CAPABILITIES))

    def execute(self, spec: QuerySpec) -> QueryResult[Any]:
        return self.runtime.execute(spec)

    def _call_core(
        self,
        capability: str,
        args: tuple[Any, ...],
        *,
        provider: str | None,
        currentness: str,
        max_age: float | None,
        kwargs: dict[str, Any],
    ) -> QueryResult[Any]:
        if capability == "quotes":
            if len(args) != 1:
                raise ValidationError("quotes requires exactly one symbols argument")
            result = self.quotes(
                args[0],
                provider=provider,
                currentness=currentness,
                max_age=max_age,
                **kwargs,
            )
        elif capability == "bars":
            if len(args) != 1:
                raise ValidationError("bars requires exactly one symbol argument")
            result = self.bars(
                str(args[0]),
                provider=provider,
                currentness=currentness,
                max_age=max_age,
                **kwargs,
            )
        elif capability == "snapshot":
            if len(args) != 1:
                raise ValidationError("snapshot requires exactly one symbol argument")
            result = self.snapshot(
                str(args[0]),
                provider=provider or "tdx",
                **kwargs,
            )
        elif capability == "minute":
            if len(args) != 1:
                raise ValidationError("minute requires exactly one symbol argument")
            result = self.minute(
                str(args[0]),
                provider=provider or "tdx",
                **kwargs,
            )
        elif capability == "trades":
            if len(args) != 1:
                raise ValidationError("trades requires exactly one symbol argument")
            result = self.trades(
                str(args[0]),
                provider=provider or "tdx",
                **kwargs,
            )
        elif capability == "security_count":
            if args:
                raise ValidationError("security_count accepts market as a keyword argument")
            result = self.security_count(
                provider=provider or "tdx",
                **kwargs,
            )
        else:
            if args:
                raise ValidationError("security_list accepts market/start as keyword arguments")
            result = self.security_list(
                provider=provider or "tdx",
                **kwargs,
            )
        if isinstance(result, OrchestratedResult):
            raise ValidationError("Client.call core path does not accept hidden fallback policy")
        return result

    def call(
        self,
        capability: str,
        *args: Any,
        provider: str | None = None,
        channel: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """Execute a core or migrated capability through the canonical runtime."""
        cap = str(capability).strip().lower()
        if cap in _CORE_CAPABILITIES:
            if channel is not None:
                raise ValidationError("Tier-A Client.call does not accept an arbitrary channel")
            core_currentness = currentness
            if currentness == "business":
                core_currentness = "live" if cap in {"quotes", "snapshot", "minute", "trades"} else "historical"
            return self._call_core(
                cap,
                args,
                provider=provider,
                currentness=core_currentness,
                max_age=max_age,
                kwargs=dict(kwargs),
            )
        if not is_migrated_capability(cap):
            raise ValidationError(
                f"未知 capability {capability!r}",
                context={"capability": cap},
            )
        selected = provider or default_provider_for(cap)
        spec = QuerySpec.build(
            cap,
            provider=selected,
            channel=channel,
            currentness=currentness,
            max_age=max_age,
            options={
                "args": _json_contract(list(args)),
                "kwargs": _json_contract(kwargs),
            },
        )
        return self.execute(spec)

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):
            def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                provider = kwargs.pop("provider", None)
                channel = kwargs.pop("channel", None)
                currentness = kwargs.pop("currentness", "business")
                max_age = kwargs.pop("max_age", None)
                return self.call(
                    name,
                    *args,
                    provider=provider,
                    channel=channel,
                    currentness=currentness,
                    max_age=max_age,
                    **kwargs,
                )

            migrated.__name__ = name
            migrated.__qualname__ = f"Client.{name}"
            return migrated
        raise AttributeError(name)

    def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        return self.orchestrator.execute(spec, policy=policy)

    def typed(self, query: Any, **kwargs: Any) -> Any:
        """Execute one typed CapabilityQuery end-to-end through the kernel.

        Compilation is fail-closed: unregistered capabilities raise before any
        Provider request, and the normalized Domain Record payload is returned.
        """
        from .typed_query import TypedQueryResult, call_payload_from_typed, records_from_data

        payload = call_payload_from_typed(query)
        payload.update(kwargs)
        result = self.call(query.capability, provider=query.provider, **payload)
        if isinstance(result, OrchestratedResult):
            raise ValidationError("Client.typed does not produce fallback-policy results")
        return TypedQueryResult(
            data=records_from_data(query.capability, result.data),
            capability=query.capability,
        )

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        policy: FallbackPolicy | None = None,
        currentness: str = "live",
        max_age: float | None = None,
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
            return self.execute_with_policy(spec, policy=policy)
        return self.execute(spec)

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> BatchResult[QueryResult[Any]]:
        return self.runtime.quotes_batch(
            symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
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
            return self.execute_with_policy(spec, policy=policy)
        return self.execute(spec)

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
    ) -> QueryResult[Any]:
        return self.runtime.snapshot(symbol, provider=provider)

    def minute(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
    ) -> QueryResult[Any]:
        return self.runtime.minute(symbol, provider=provider)

    def trades(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
        start: int = 0,
        count: int = 0,
    ) -> QueryResult[Any]:
        return self.runtime.trades(
            symbol,
            provider=provider,
            start=start,
            count=count,
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str = "tdx",
    ) -> QueryResult[Any]:
        return self.runtime.security_count(
            market=market,
            provider=provider,
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str = "tdx",
    ) -> QueryResult[Any]:
        return self.runtime.security_list(
            market=market,
            start=start,
            provider=provider,
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

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return Client.capabilities()

    async def execute(
        self,
        spec: QuerySpec,
    ) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.execute, spec)

    async def call(
        self,
        capability: str,
        *args: Any,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.call, capability, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):
            async def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                return await asyncio.to_thread(
                    getattr(self.client, name),
                    *args,
                    **kwargs,
                )

            migrated.__name__ = name
            migrated.__qualname__ = f"AsyncClient.{name}"
            return migrated
        raise AttributeError(name)

    async def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        return await asyncio.to_thread(
            self.client.execute_with_policy,
            spec,
            policy=policy,
        )

    async def typed(self, query: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.typed, query, **kwargs)

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
