# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Async facade over the canonical planned Provider-bound service.

Provider adapters are predominantly synchronous. ``AsyncMarketDataService``
executes the same QueryPlan-backed sync service through a bounded worker pool,
so Provider/freshness/deadline/singleflight/batch semantics remain identical
without maintaining a second routing implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, TypeVar

from .planned_service import UnifiedMarketDataService
from .providers import resolve_provider
from .query import QuerySpec

__all__ = ["AsyncMarketDataService", "AsyncProviderAPI", "async_market_data"]

T = TypeVar("T")


class AsyncProviderAPI:
    """Async Direct Provider namespace backed by the same sync ProviderAPI."""

    def __init__(self, owner: "AsyncMarketDataService", provider: str) -> None:
        self._owner = owner
        self.provider = resolve_provider(provider=provider)

    @property
    def spec(self) -> Any:
        return self._owner.sync.provider(self.provider).spec

    async def quotes(self, *args: Any, **kwargs: Any) -> Any:
        return await self._owner._run(
            self._owner.sync.provider(self.provider).quotes,
            *args,
            **kwargs,
        )

    async def bars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._owner._run(
            self._owner.sync.provider(self.provider).bars,
            *args,
            **kwargs,
        )

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        target = getattr(self._owner.sync.provider(self.provider), method)
        if not callable(target):
            raise AttributeError(method)
        return await self._owner._run(target, *args, **kwargs)


class AsyncMarketDataService:
    """Bounded async execution of one canonical planned market-data service."""

    def __init__(
        self,
        *,
        service: UnifiedMarketDataService | None = None,
        max_workers: int = 8,
        max_concurrency: int | None = None,
        **service_kwargs: Any,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be > 0")
        concurrency = max_concurrency if max_concurrency is not None else max_workers
        if concurrency <= 0:
            raise ValueError("max_concurrency must be > 0")
        self.sync = service if service is not None else UnifiedMarketDataService(**service_kwargs)
        self._owns_service = service is None
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="tstdx-async-provider",
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._closed = False
        self._providers: dict[str, AsyncProviderAPI] = {}

    async def _run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._closed:
            raise RuntimeError("AsyncMarketDataService 已关闭")
        loop = asyncio.get_running_loop()
        call = partial(fn, *args, **kwargs)
        async with self._semaphore:
            return await loop.run_in_executor(self._executor, call)

    async def query(self, spec: QuerySpec, *, with_meta: bool = True) -> Any:
        """Execute the same canonical QuerySpec path as the sync service."""
        return await self._run(self.sync.query, spec, with_meta=with_meta)

    async def query_many(
        self,
        specs: Sequence[QuerySpec],
        *,
        with_meta: bool = True,
    ) -> list[Any]:
        """Execute canonical multi-query planning through the sync planned core."""
        return await self._run(self.sync.query_many, specs, with_meta=with_meta)

    async def quotes(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run(self.sync.quotes, *args, **kwargs)

    async def bars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run(self.sync.bars, *args, **kwargs)

    def provider(self, provider: str) -> AsyncProviderAPI:
        pid = resolve_provider(provider=provider)
        namespace = self._providers.get(pid)
        if namespace is None:
            self.sync.provider(pid)
            namespace = AsyncProviderAPI(self, pid)
            self._providers[pid] = namespace
        return namespace

    @property
    def tdx(self) -> AsyncProviderAPI:
        return self.provider("tdx")

    @property
    def tencent(self) -> AsyncProviderAPI:
        return self.provider("tencent")

    @property
    def sina(self) -> AsyncProviderAPI:
        return self.provider("sina")

    @property
    def eastmoney(self) -> AsyncProviderAPI:
        return self.provider("eastmoney")

    @property
    def baidu(self) -> AsyncProviderAPI:
        return self.provider("baidu")

    @property
    def jsl(self) -> AsyncProviderAPI:
        return self.provider("jsl")

    @property
    def boc(self) -> AsyncProviderAPI:
        return self.provider("boc")

    @property
    def iwencai(self) -> AsyncProviderAPI:
        return self.provider("iwencai")

    async def aclose(self) -> None:
        if self._closed:
            return
        try:
            if self._owns_service:
                await self._run(self.sync.close)
        finally:
            self._closed = True
            self._executor.shutdown(wait=True, cancel_futures=False)

    async def __aenter__(self) -> "AsyncMarketDataService":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


def async_market_data(**kwargs: Any) -> AsyncMarketDataService:
    return AsyncMarketDataService(**kwargs)
