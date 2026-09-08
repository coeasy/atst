# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Strict legacy-shaped facade backed by the planned v12 service."""

from __future__ import annotations

from typing import Any

from ..planned_service import UnifiedMarketDataService
from .strict import UnifiedQuoteAPI as StrictUnifiedQuoteAPI

__all__ = ["UnifiedQuoteAPI", "StrictUnifiedQuoteAPI", "quote_api"]


class UnifiedQuoteAPI(StrictUnifiedQuoteAPI):
    """Official compatibility facade using QueryPlan/SingleFlight execution."""

    def _get_provider_service(self) -> UnifiedMarketDataService:
        if self._provider_service is None:
            self._provider_service = UnifiedMarketDataService(
                hosts=self.hosts,
                timeout=self.timeout,
            )
        return self._provider_service  # type: ignore[return-value]


def quote_api(**kwargs: Any) -> UnifiedQuoteAPI:
    return UnifiedQuoteAPI(**kwargs)
