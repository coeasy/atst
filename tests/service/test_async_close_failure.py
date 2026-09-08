from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tstdx.async_service import AsyncMarketDataService
from tstdx.facade.strict_async import AsyncUnifiedQuoteAPI


class FailingCloseService:
    def close(self) -> None:
        raise RuntimeError("sync close failed")

    def quotes(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class FailingCloseFacade:
    def close(self) -> None:
        raise RuntimeError("facade close failed")

    def quotes(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_owned_sync_close_failure_still_closes_async_executor() -> None:
    sync = FailingCloseService()
    service = AsyncMarketDataService(service=sync, max_workers=1)  # type: ignore[arg-type]
    service._owns_service = True

    with pytest.raises(RuntimeError, match="sync close failed"):
        await service.aclose()

    assert service._closed is True
    with pytest.raises(RuntimeError, match="已关闭"):
        await service.quotes(["sh600519"])


@pytest.mark.asyncio
async def test_async_close_drains_submitted_work_and_rejects_queued_work() -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    class BlockingService:
        def quotes(self, *args: Any, **kwargs: Any) -> list[Any]:
            order.append("quote_start")
            started.set()
            release.wait(1.0)
            order.append("quote_end")
            return []

        def close(self) -> None:
            order.append("close")

    sync = BlockingService()
    service = AsyncMarketDataService(
        service=sync,  # type: ignore[arg-type]
        max_workers=1,
        max_concurrency=1,
    )
    service._owns_service = True

    first = asyncio.create_task(service.quotes(["sh600519"]))
    assert await asyncio.to_thread(started.wait, 1.0)
    queued = asyncio.create_task(service.quotes(["sz000001"]))
    await asyncio.sleep(0)
    closing = asyncio.create_task(service.aclose())
    await asyncio.sleep(0.02)

    assert "close" not in order
    release.set()
    await first
    with pytest.raises(RuntimeError, match="已关闭"):
        await queued
    await closing

    assert order == ["quote_start", "quote_end", "close"]


@pytest.mark.asyncio
async def test_strict_async_close_failure_still_marks_wrapper_closed() -> None:
    service = AsyncUnifiedQuoteAPI()
    service._sync = FailingCloseFacade()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="facade close failed"):
        await service.aclose()

    assert service._closed is True
    with pytest.raises(RuntimeError, match="已关闭"):
        await service.quotes(["sh600519"])
