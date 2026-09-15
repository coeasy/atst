# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Async compatibility wrapper for the planned strict Provider-bound facade."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any, TypeVar

from ..query import QuerySpec
from .planned import UnifiedQuoteAPI

__all__ = ["AsyncUnifiedQuoteAPI"]

T = TypeVar("T")


async def _await_thread_call(worker: asyncio.Task[T]) -> T:
    """Wait for the real sync call to finish before releasing active-call state."""
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        with contextlib.suppress(BaseException):
            worker.result()
        raise


class AsyncUnifiedQuoteAPI:
    """Run the planned strict sync facade without duplicating routing logic.

    This class remains a compatibility layer. New async applications should use
    :class:`tstdx.async_service.AsyncMarketDataService`.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._sync = UnifiedQuoteAPI(*args, **kwargs)
        self._state_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
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
        worker = asyncio.create_task(asyncio.to_thread(partial(fn, *args, **kwargs)))
        try:
            return await _await_thread_call(worker)
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

    async def _finish_close(self) -> None:
        await self._drained.wait()
        await asyncio.to_thread(self._sync.close)

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._close_task is None:
                async with self._state_lock:
                    self._closed = True
                self._close_task = asyncio.create_task(self._finish_close())
            close_task = self._close_task
        await _await_thread_call(close_task)

    async def __aenter__(self) -> AsyncUnifiedQuoteAPI:
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
