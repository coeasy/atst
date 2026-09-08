from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tstdx.facade.strict_async import AsyncUnifiedQuoteAPI


@pytest.mark.asyncio
async def test_cancelled_strict_call_stays_active_until_sync_thread_finishes() -> None:
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    class BlockingFacade:
        def quotes(self, *args: Any, **kwargs: Any) -> list[Any]:
            order.append("quote_start")
            started.set()
            release.wait(1.0)
            order.append("quote_end")
            return []

        def close(self) -> None:
            order.append("close")

    service = AsyncUnifiedQuoteAPI()
    service._sync = BlockingFacade()  # type: ignore[assignment]

    active = asyncio.create_task(service.quotes(["sh600519"]))
    assert await asyncio.to_thread(started.wait, 1.0)
    active.cancel()
    closing = asyncio.create_task(service.aclose())
    await asyncio.sleep(0.05)

    assert "close" not in order
    assert service._active_calls == 1

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await active
    await closing

    assert service._active_calls == 0
    assert order == ["quote_start", "quote_end", "close"]
