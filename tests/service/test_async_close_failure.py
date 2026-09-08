from __future__ import annotations

from typing import Any

import pytest

from tstdx.async_service import AsyncMarketDataService


class FailingCloseService:
    def close(self) -> None:
        raise RuntimeError("sync close failed")

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
