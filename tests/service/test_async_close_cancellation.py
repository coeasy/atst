from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tstdx.async_service import AsyncMarketDataService


@pytest.mark.asyncio
async def test_cancelled_aclose_waits_for_real_shutdown_before_propagating_cancel() -> None:
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

    active = asyncio.create_task(service.quotes(["sh600519"]))
    assert await asyncio.to_thread(started.wait, 1.0)

    closing = asyncio.create_task(service.aclose())
    await asyncio.sleep(0.02)
    closing.cancel()
    await asyncio.sleep(0.05)

    assert "close" not in order
    assert closing.done() is False

    release.set()
    await active
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert order == ["quote_start", "quote_end", "close"]
    await service.aclose()
    assert order == ["quote_start", "quote_end", "close"]
