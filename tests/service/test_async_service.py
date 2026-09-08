from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tstdx.async_service import AsyncMarketDataService
from tstdx.facade import (
    AsyncUnifiedQuoteAPI,
    LegacyAsyncUnifiedQuoteAPI,
)
from tstdx.facade.strict_async import AsyncUnifiedQuoteAPI as StrictAsyncFacade


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def quotes(self, symbols, *, provider=None, source=None, **kwargs):  # noqa: ANN001
        pid = provider or source or "tdx"
        self.calls.append(("quotes", pid))
        return [{"provider": pid, "symbols": tuple(symbols)}]

    def bars(self, symbol, *, provider=None, source=None, **kwargs):  # noqa: ANN001
        pid = provider or source or "tdx"
        self.calls.append(("bars", pid))
        return [{"provider": pid, "symbol": symbol}]

    def provider(self, provider: str):
        owner = self

        class Direct:
            spec = {"id": provider}

            def quotes(self, symbols, **kwargs):  # noqa: ANN001
                return owner.quotes(symbols, provider=provider, **kwargs)

            def bars(self, symbol, **kwargs):  # noqa: ANN001
                return owner.bars(symbol, provider=provider, **kwargs)

            def ping(self):
                owner.calls.append(("ping", provider))
                return provider

        return Direct()

    def close(self) -> None:
        self.closed = True


def test_official_async_facade_is_strict_and_legacy_remains_available() -> None:
    assert AsyncUnifiedQuoteAPI is StrictAsyncFacade
    assert AsyncUnifiedQuoteAPI is not LegacyAsyncUnifiedQuoteAPI


@pytest.mark.asyncio
async def test_async_service_preserves_explicit_provider() -> None:
    sync = FakeSyncService()
    service = AsyncMarketDataService(service=sync, max_workers=2)  # type: ignore[arg-type]
    try:
        rows = await service.quotes(["sh600519"], provider="tencent")
        bars = await service.bars("sh600519", provider="tdx")
        assert rows[0]["provider"] == "tencent"
        assert bars[0]["provider"] == "tdx"
        assert sync.calls == [("quotes", "tencent"), ("bars", "tdx")]
    finally:
        await service.aclose()
    # External service lifecycle is not owned by async wrapper.
    assert sync.closed is False


@pytest.mark.asyncio
async def test_async_direct_provider_call_stays_provider_bound() -> None:
    sync = FakeSyncService()
    service = AsyncMarketDataService(service=sync, max_workers=1)  # type: ignore[arg-type]
    try:
        result = await service.tencent.call("ping")
        assert result == "tencent"
        assert sync.calls == [("ping", "tencent")]
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_async_service_concurrency_is_bounded_by_worker_pool() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()
    gate = threading.Event()

    class BlockingService(FakeSyncService):
        def quotes(self, symbols, *, provider=None, source=None, **kwargs):  # noqa: ANN001
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                gate.wait(1.0)
                return []
            finally:
                with lock:
                    active -= 1

    sync = BlockingService()
    service = AsyncMarketDataService(
        service=sync,  # type: ignore[arg-type]
        max_workers=2,
        max_concurrency=2,
    )
    try:
        tasks = [asyncio.create_task(service.quotes([str(i)])) for i in range(6)]
        await asyncio.sleep(0.05)
        assert peak <= 2
        gate.set()
        await asyncio.gather(*tasks)
    finally:
        gate.set()
        await service.aclose()


@pytest.mark.asyncio
async def test_async_service_rejects_new_work_after_close() -> None:
    sync = FakeSyncService()
    service = AsyncMarketDataService(service=sync, max_workers=1)  # type: ignore[arg-type]
    await service.aclose()
    with pytest.raises(RuntimeError, match="已关闭"):
        await service.quotes(["sh600519"])
