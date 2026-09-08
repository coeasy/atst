# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""High-level public APIs.

``tstdx.facade.UnifiedQuoteAPI`` is the v12 strict, Provider-bound compatibility
facade. The exact pre-v12 class remains available as ``LegacyUnifiedQuoteAPI``
and from :mod:`tstdx.facade.api` for callers intentionally preserving historical
routing semantics during migration.

New applications should prefer :class:`tstdx.service.UnifiedMarketDataService`
and Direct Provider namespaces (``md.tdx``, ``md.tencent``, ``md.sina``, ...).
The existing binary/bridge/market convenience facades remain public for
compatibility and are migrated independently.
"""

from __future__ import annotations

from ..service import UnifiedMarketDataService, market_data
from .api import UnifiedQuoteAPI as LegacyUnifiedQuoteAPI
from .async_api import AsyncUnifiedQuoteAPI
from .binary import TDX_CATEGORY_TO_PERIOD, BinaryClient, binary_client
from .bridge import FREQUENCY_ALIASES, BridgeClient, bridge_client
from .market import (
    FREQUENCY_PERIOD_MAP,
    ExHqClient,
    HqClient,
    OptionClient,
    market_client,
)
from .response import ApiResponse, err, ok, wrap
from .strict import UnifiedQuoteAPI, quote_api

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
