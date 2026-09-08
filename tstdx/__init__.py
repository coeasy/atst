# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""tstdx —— TDX-first, multi-provider market-data library.

The v12 public architecture uses one canonical provider model and one planned
execution path::

    QuerySpec -> QueryPlan -> Provider -> Channel -> Capability -> Endpoint/Host

TDX is the default Provider. Tencent/Sina/Eastmoney/... are independent
Providers, never implicit fallbacks. Public high-level queries compile before
I/O, bind one Provider/Channel, carry one total deadline and may coalesce only
identical semantic fingerprints. Low-level protocol/reader APIs remain public.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.4.0"

__all__ = [
    "__version__",
    "TdxClient",
    "AsyncTdxClient",
    "WebQuoteClient",
    "DayBarReader",
    "MinBarReader",
    "BlockReader",
    "FinanceReader",
    "DataProfile",
    "ChannelSpec",
    "ProviderSpec",
    "ProviderRegistry",
    "PROVIDERS",
    "resolve_provider",
    "QuerySpec",
    "QueryPlan",
    "QueryPlanner",
    "QueryFingerprint",
    "UnifiedMarketDataService",
    "market_data",
    "AsyncMarketDataService",
    "async_market_data",
    "FailureDisposition",
    "FailurePolicy",
    "ErrorEnvelope",
    "PlannedQuoteStream",
    "configure",
    "get_config",
    "load_config",
    "providers",
    "facade",
    "observability",
    "streaming",
]

from . import codec, errors, protocol  # noqa: E402,F401
from .errors import TdxError  # noqa: E402,F401


def configure(**kwargs: Any) -> Any:
    """Override global configuration with keyword arguments."""
    from .config import load_config

    return load_config(overrides=kwargs)


def get_config() -> Any:
    """Return the effective global configuration."""
    from .config import get_config as _get

    return _get()


if TYPE_CHECKING:  # pragma: no cover
    from .async_service import AsyncMarketDataService
    from .client import AsyncTdxClient, TdxClient
    from .config import load_config
    from .error_envelope import ErrorEnvelope
    from .failure import FailureDisposition, FailurePolicy
    from .planned_service import UnifiedMarketDataService
    from .providers import ChannelSpec, ProviderRegistry, ProviderSpec
    from .query import QueryFingerprint, QueryPlan, QueryPlanner, QuerySpec
    from .reader import BlockReader, DataProfile, DayBarReader, FinanceReader, MinBarReader
    from .streaming.planned import PlannedQuoteStream
    from .web import WebQuoteClient


_LAZY: dict[str, tuple[str, str]] = {
    "TdxClient": ("tstdx.client", "TdxClient"),
    "AsyncTdxClient": ("tstdx.client", "AsyncTdxClient"),
    "WebQuoteClient": ("tstdx.web", "WebQuoteClient"),
    "DayBarReader": ("tstdx.reader", "DayBarReader"),
    "MinBarReader": ("tstdx.reader", "MinBarReader"),
    "BlockReader": ("tstdx.reader", "BlockReader"),
    "FinanceReader": ("tstdx.reader", "FinanceReader"),
    "DataProfile": ("tstdx.reader", "DataProfile"),
    "load_config": ("tstdx.config", "load_config"),
    "ChannelSpec": ("tstdx.providers", "ChannelSpec"),
    "ProviderSpec": ("tstdx.providers", "ProviderSpec"),
    "ProviderRegistry": ("tstdx.providers", "ProviderRegistry"),
    "PROVIDERS": ("tstdx.providers", "PROVIDERS"),
    "resolve_provider": ("tstdx.providers", "resolve_provider"),
    "QuerySpec": ("tstdx.query", "QuerySpec"),
    "QueryPlan": ("tstdx.query", "QueryPlan"),
    "QueryPlanner": ("tstdx.query", "QueryPlanner"),
    "QueryFingerprint": ("tstdx.query", "QueryFingerprint"),
    "UnifiedMarketDataService": ("tstdx.planned_service", "UnifiedMarketDataService"),
    "market_data": ("tstdx.planned_service", "market_data"),
    "AsyncMarketDataService": ("tstdx.async_service", "AsyncMarketDataService"),
    "async_market_data": ("tstdx.async_service", "async_market_data"),
    "FailureDisposition": ("tstdx.failure", "FailureDisposition"),
    "FailurePolicy": ("tstdx.failure", "FailurePolicy"),
    "ErrorEnvelope": ("tstdx.error_envelope", "ErrorEnvelope"),
    "PlannedQuoteStream": ("tstdx.streaming.planned", "PlannedQuoteStream"),
    "providers": ("tstdx.providers", ""),
    "facade": ("tstdx.facade", ""),
    "observability": ("tstdx.observability", ""),
    "streaming": ("tstdx.streaming", ""),
    "deprecated": ("tstdx.deprecation", "deprecated"),
    "DeprecationPolicy": ("tstdx.deprecation", "DeprecationPolicy"),
    "FeedbackReporter": ("tstdx.feedback", "FeedbackReporter"),
    "TelemetryCollector": ("tstdx.feedback", "TelemetryCollector"),
    "UserStats": ("tstdx.feedback", "UserStats"),
    "detect_encoding": ("tstdx.charset.encoding", "detect_encoding"),
    "decode_bytes": ("tstdx.charset.encoding", "decode_bytes"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod_path, attr = _LAZY[name]
        import importlib

        mod = importlib.import_module(mod_path)
        value = mod if not attr else getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
