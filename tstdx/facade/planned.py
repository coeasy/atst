# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Strict legacy-shaped facade backed by the planned v12 service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..planned_service import UnifiedMarketDataService
from ..query import QuerySpec
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

    def query(self, spec: QuerySpec, *, with_meta: bool = True) -> Any:
        """Expose the canonical planned query contract without another router."""
        return self._get_provider_service().query(spec, with_meta=with_meta)

    def query_many(
        self,
        specs: Sequence[QuerySpec],
        *,
        with_meta: bool = True,
    ) -> list[Any]:
        """Expose canonical multi-query execution through the same service."""
        return self._get_provider_service().query_many(specs, with_meta=with_meta)


def quote_api(**kwargs: Any) -> UnifiedQuoteAPI:
    return UnifiedQuoteAPI(**kwargs)
