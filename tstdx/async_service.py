# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Async facade over the canonical Provider-bound service.

The current Provider adapters are predominantly synchronous.  Instead of
maintaining a second routing implementation, ``AsyncMarketDataService`` executes
the same :class:`UnifiedMarketDataService` through a bounded worker pool.  This
preserves Provider/freshness/error semantics and prevents one thread per call.

Native async Provider adapters can later replace individual calls without
changing Query/Provider semantics.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar

from .providers import resolve_provider
from .service import UnifiedMarketDataService

__all__ = ["AsyncMarketDataService", "AsyncProviderAPI", "async_market_data"]

T = TypeVar("T")


class AsyncProviderAPI:
    """Async Direct Provider namespace backed by the same sync ProviderAPI."""

    def __init__(self, owner: AsyncMarketDataService, provider: str) -> None:
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
        """Call one explicit method on this Provider namespace.

        This is not arbitrary global dispatch: lookup is scoped to the already
        selected Provider object. A missing method fails before any I/O.
        """
        target = getattr(self._owner.sync.provider(self.provider), method)
        if not callable(target):
            raise AttributeError(method)
        return await self._owner._run(target, *args, **kwargs)


class AsyncMarketDataService:
    """Bounded async execution of one canonical market-data service."""

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
        self.sync = service or UnifiedMarketDataService(**service_kwargs)
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

    async def quotes(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run(self.sync.quotes, *args, **kwargs)

    async def bars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._run(self.sync.bars, *args, **kwargs)

    def provider(self, provider: str) -> AsyncProviderAPI:
        pid = resolve_provider(provider=provider)
        namespace = self._providers.get(pid)
        if namespace is None:
            # Validate against the canonical sync registry/API without I/O.
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
        if self._owns_service:
            await self._run(self.sync.close)
        self._closed = True
        # At this point the semaphore-protected close has completed and callers
        # should no longer enqueue work. Executor shutdown therefore only joins
        # already-finished/returning workers.
        self._executor.shutdown(wait=True, cancel_futures=False)

    async def __aenter__(self) -> AsyncMarketDataService:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()


def async_market_data(**kwargs: Any) -> AsyncMarketDataService:
    return AsyncMarketDataService(**kwargs)
