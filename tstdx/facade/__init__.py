# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""High-level public APIs.

The official compatibility facade is strict and QueryPlan-backed. The exact
pre-v12 classes remain importable from :mod:`tstdx.facade.api` and
:mod:`tstdx.facade.async_api` for callers intentionally preserving historical
routing behavior during migration.

New applications should prefer :class:`tstdx.planned_service.UnifiedMarketDataService`
and Direct Provider namespaces (``md.tdx``, ``md.tencent``, ``md.sina``, ...).
"""

from __future__ import annotations

from ..planned_service import UnifiedMarketDataService, market_data
from .api import UnifiedQuoteAPI as LegacyUnifiedQuoteAPI
from .binary import TDX_CATEGORY_TO_PERIOD, BinaryClient, binary_client
from .bridge import FREQUENCY_ALIASES, BridgeClient, bridge_client
from .market import (
    FREQUENCY_PERIOD_MAP,
    ExHqClient,
    HqClient,
    OptionClient,
    market_client,
)
from .planned import UnifiedQuoteAPI, quote_api
from .response import ApiResponse, err, ok, wrap
from .strict_async import AsyncUnifiedQuoteAPI

__all__ = [
    "UnifiedQuoteAPI",
    "LegacyUnifiedQuoteAPI",
    "AsyncUnifiedQuoteAPI",
    "UnifiedMarketDataService",
    "market_data",
    "quote_api",
    "BinaryClient",
    "binary_client",
    "TDX_CATEGORY_TO_PERIOD",
    "BridgeClient",
    "bridge_client",
    "FREQUENCY_ALIASES",
    "HqClient",
    "ExHqClient",
    "OptionClient",
    "market_client",
    "FREQUENCY_PERIOD_MAP",
    "ApiResponse",
    "ok",
    "err",
    "wrap",
]
