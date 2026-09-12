# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""High-level facade APIs."""

from .api import UnifiedQuoteAPI, quote_api
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
from .runtime_adapter import RuntimeFacadeAdapter

__all__ = [
    "UnifiedQuoteAPI",
    "AsyncUnifiedQuoteAPI",
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
    "RuntimeFacadeAdapter",
]
