# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Async compatibility wrapper for the planned strict Provider-bound facade."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, TypeVar

from ..query import QuerySpec
from .planned import UnifiedQuoteAPI

__all__ = ["AsyncUnifiedQuoteAPI"]

T = TypeVar("T")


class AsyncUnifiedQuoteAPI:
    """Run the planned strict sync facade without duplicating routing logic.

    This class remains a compatibility layer. New async applications should use
    :class:`tstdx.async_service.AsyncMarketDataService`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._sync = UnifiedQuoteAPI(*args, **kwargs)
        self._state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()
        self._active_calls = 0
        self._closed = False

    @property
    def sync(self) -> UnifiedQuoteAPI:
        return self._sync

    async def _call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        async with self._state_lock:
            if self._closed:
                raise RuntimeError("AsyncUnifiedQuoteAPI 已关闭")
            self._active_calls += 1
            if self._active_calls == 1:
                self._drained.clear()
        try:
            return await asyncio.to_thread(partial(fn, *args, **kwargs))
        finally:
            async with self._state_lock:
                self._active_calls -= 1
                if self._active_calls == 0:
                    self._drained.set()

    async def query(self, spec: QuerySpec, *, with_meta: bool = True) -> Any:
        return await self._call(self._sync.query, spec, with_meta=with_meta)

    async def query_many(
        self,
        specs: Sequence[QuerySpec],
        *,
        with_meta: bool = True,
    ) -> list[Any]:
        return await self._call(self._sync.query_many, specs, with_meta=with_meta)

    async def quotes(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.quotes, *args, **kwargs)

    async def bars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.bars, *args, **kwargs)

    async def minute(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.minute, *args, **kwargs)

    async def trades(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.trades, *args, **kwargs)

    async def security_list(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.security_list, *args, **kwargs)

    async def security_list_all(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.security_list_all, *args, **kwargs)

    async def adjusted_bars(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call(self._sync.adjusted_bars, *args, **kwargs)

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        target = getattr(self._sync, method)
        if not callable(target):
            raise AttributeError(method)
        return await self._call(target, *args, **kwargs)

    async def aclose(self) -> None:
        async with self._close_lock:
            async with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                active = self._active_calls
            if active:
                await self._drained.wait()
            await asyncio.to_thread(self._sync.close)

    async def __aenter__(self) -> "AsyncUnifiedQuoteAPI":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._sync, name)
        if callable(attr):

            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                return await self._call(attr, *args, **kwargs)

            return wrapper
        return attr
