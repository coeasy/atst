from __future__ import annotations

import asyncio
import threading

import pytest

from tstdx.async_service import AsyncMarketDataService
from tstdx.facade import (
    AsyncUnifiedQuoteAPI,
)
from tstdx.facade.strict_async import AsyncUnifiedQuoteAPI as StrictAsyncFacade
from tstdx.query import QuerySpec


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def query(self, spec: QuerySpec, *, with_meta: bool = True):  # noqa: ANN201
        pid = spec.provider or spec.source or "tdx"
        self.calls.append(("query", pid))
        return {"provider": pid, "capability": spec.capability, "with_meta": with_meta}

    def query_many(self, specs, *, with_meta: bool = True):  # noqa: ANN001,ANN201
        self.calls.append(("query_many", str(len(specs))))
        return [
            {
                "provider": spec.provider or spec.source or "tdx",
                "capability": spec.capability,
                "with_meta": with_meta,
            }
            for spec in specs
        ]

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


def test_official_async_facade_is_strict_canonical() -> None:
    # v15：facade 仅保留严格版 AsyncUnifiedQuoteAPI（strict_async 即其实现），
    # 旧的非严格 LegacyAsyncUnifiedQuoteAPI 已在 clean-break 中合并移除。
    assert AsyncUnifiedQuoteAPI is StrictAsyncFacade


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
    assert sync.closed is False


@pytest.mark.asyncio
async def test_async_query_and_query_many_delegate_to_same_sync_planned_core() -> None:
    sync = FakeSyncService()
    service = AsyncMarketDataService(service=sync, max_workers=2)  # type: ignore[arg-type]
    quote_spec = QuerySpec.build("quotes", symbols=["sh600519"], provider="tencent")
    bar_spec = QuerySpec.build(
        "bars", symbols=["sh600519"], provider="tdx", count=10
    )
    try:
        one = await service.query(quote_spec, with_meta=False)
        many = await service.query_many([quote_spec, bar_spec], with_meta=True)
        assert one == {
            "provider": "tencent",
            "capability": "quotes",
            "with_meta": False,
        }
        assert [item["provider"] for item in many] == ["tencent", "tdx"]
        assert [item["capability"] for item in many] == ["quotes", "bars"]
        assert sync.calls == [("query", "tencent"), ("query_many", "2")]
    finally:
        await service.aclose()


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
