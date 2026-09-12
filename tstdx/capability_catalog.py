# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 migrated capability catalog.

This module is the migration SSOT for business abilities that existed on the
retired UnifiedQuoteAPI surface.  The old facade is not restored.  Each entry
binds one capability to one explicit Provider/Channel and one low-level provider
implementation.  QuerySpec options carry only deterministic JSON call arguments;
execution still flows through QueryPlanner -> UnifiedRuntime -> DirectBinding.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

__all__ = [
    "MigratedCapabilityBinding",
    "MIGRATED_BINDINGS",
    "MIGRATED_CAPABILITIES",
    "binding_for",
    "bindings_for_provider",
    "default_provider_for",
    "is_migrated_capability",
]


@dataclass(frozen=True, slots=True)
class MigratedCapabilityBinding:
    capability: str
    provider: str
    channel: str
    backend: str
    method: str
    source: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


# Public WebQuoteSession methods are provider implementations, not a second
# business API.  Most of the historical extended methods are Eastmoney-backed;
# the explicit overrides below preserve known upstream identity.
_PROVIDER_OVERRIDES: dict[str, str] = {
    "industry_boards": "sina",
    "board_list": "sina",
    "board_members": "sina",
    "suggest": "sina",
    "search_symbols": "sina",
    "esg_rating": "sina",
    "esg_history": "sina",
    "esg_ratings_all": "sina",
    "board_rank": "tencent",
    "rates": "boc",
    "wencai": "iwencai",
    "index_list": "builtin",
    "dc_reports": "builtin",
}

_SOURCE_FOR_PROVIDER: dict[str, str] = {
    "eastmoney": "eastmoney",
    "sina": "sina",
    "tencent": "tencent",
    "baidu": "sina",   # Baidu mixin constructs its own Baidu source.
    "boc": "boc",
    "iwencai": "sina", # static/provider-specific implementation ignores session source.
    "builtin": "sina", # offline/static helpers ignore session source.
}

_SKIP_WEB_METHODS = {
    "close",
    "quotes",  # canonical Tier-A binding already exists.
}


def _discover_web_bindings() -> list[MigratedCapabilityBinding]:
    from .web.facade import WebQuoteSession

    values: list[MigratedCapabilityBinding] = []
    for name, member in inspect.getmembers(WebQuoteSession, predicate=callable):
        if name.startswith("_") or name in _SKIP_WEB_METHODS:
            continue
        provider = _PROVIDER_OVERRIDES.get(name)
        if provider is None:
            provider = "baidu" if name.startswith("baidu_") else "eastmoney"
        values.append(
            MigratedCapabilityBinding(
                capability=name,
                provider=provider,
                channel="catalog",
                backend="web_session",
                method=name,
                source=_SOURCE_FOR_PROVIDER[provider],
            )
        )
    return values


# Capabilities that historically lived in UnifiedQuoteAPI rather than
# WebQuoteSession.  These are migrated onto exact low-level provider clients or
# explicit composed executors; no route/auto/facade code is retained.
_EXPLICIT_BINDINGS: tuple[MigratedCapabilityBinding, ...] = (
    MigratedCapabilityBinding("quotes_concurrent", "tdx", "quotation_aux", "tdx_client", "quotes_concurrent"),
    MigratedCapabilityBinding("minute_history", "tdx", "quotation_aux", "tdx_client", "minute_history"),
    MigratedCapabilityBinding("block_quotes", "tdx", "quotation_aux", "tdx_client", "block_quotes"),
    MigratedCapabilityBinding("auction", "tdx", "quotation_aux", "tdx_client", "auction_snapshot"),
    MigratedCapabilityBinding("volume_price", "tdx", "quotation_aux", "tdx_client", "volume_price_dist"),
    MigratedCapabilityBinding("finance", "tdx", "finance", "tdx_client", "finance_info"),
    MigratedCapabilityBinding("capital_changes", "tdx", "finance", "tdx_client", "capital_changes"),
    MigratedCapabilityBinding("corporate_action", "tdx", "finance", "tdx_client", "capital_changes"),
    MigratedCapabilityBinding("adjusted_bars", "tdx", "finance", "composed", "adjusted_bars"),
    MigratedCapabilityBinding("security_list_all", "tdx", "quotation_aux", "composed", "security_list_all"),
    MigratedCapabilityBinding("sync_daily", "tdx", "sync", "composed", "sync_daily"),
    MigratedCapabilityBinding("f10", "tdx", "f10", "f10_client", "f10"),
    MigratedCapabilityBinding("f10_catalog", "tdx", "f10", "f10_client", "catalog"),
    MigratedCapabilityBinding("ex_market_list", "tdx", "extended", "ex_client", "market_list"),
    MigratedCapabilityBinding("ex_instruments", "tdx", "extended", "ex_client", "instrument_list"),
    MigratedCapabilityBinding("ex_bars", "tdx", "extended", "ex_client", "ex_bars"),
    MigratedCapabilityBinding("ex_quotes", "tdx", "extended", "ex_client", "ex_quote"),
    MigratedCapabilityBinding("goods_bars", "tdx", "goods", "goods_client", "goods_bars"),
    MigratedCapabilityBinding("goods_quotes", "tdx", "goods", "goods_client", "goods_quote"),
    MigratedCapabilityBinding("minute_web", "tencent", "catalog", "web_adapter", "minute_web"),
    MigratedCapabilityBinding("minute_klines", "tencent", "catalog", "web_adapter", "minute_klines"),
    MigratedCapabilityBinding("minute_klines", "eastmoney", "catalog", "web_adapter", "minute_klines"),
    MigratedCapabilityBinding("history", "sina", "catalog", "web_adapter", "history"),
    MigratedCapabilityBinding("history", "eastmoney", "catalog", "web_adapter", "history"),
)


def _build_bindings() -> tuple[MigratedCapabilityBinding, ...]:
    # Explicit bindings win over discovered convenience methods for an identical
    # provider/channel/capability key.
    by_key: dict[tuple[str, str, str], MigratedCapabilityBinding] = {
        item.key: item for item in _discover_web_bindings()
    }
    for item in _EXPLICIT_BINDINGS:
        by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda x: x.key))


MIGRATED_BINDINGS = _build_bindings()
MIGRATED_CAPABILITIES = frozenset(item.capability for item in MIGRATED_BINDINGS)
_BINDINGS_BY_KEY = MappingProxyType({item.key: item for item in MIGRATED_BINDINGS})
_BINDINGS_BY_CAPABILITY: dict[str, tuple[MigratedCapabilityBinding, ...]] = {}
for _capability in sorted(MIGRATED_CAPABILITIES):
    _BINDINGS_BY_CAPABILITY[_capability] = tuple(
        item for item in MIGRATED_BINDINGS if item.capability == _capability
    )


def binding_for(provider: str, channel: str, capability: str) -> MigratedCapabilityBinding:
    return _BINDINGS_BY_KEY[(provider, channel, capability)]


def bindings_for_provider(provider: str) -> tuple[MigratedCapabilityBinding, ...]:
    pid = str(provider).strip().lower()
    return tuple(item for item in MIGRATED_BINDINGS if item.provider == pid)


def default_provider_for(capability: str) -> str:
    cap = str(capability).strip().lower()
    values = _BINDINGS_BY_CAPABILITY.get(cap)
    if not values:
        raise KeyError(cap)
    # Prefer tdx where the historical capability was protocol-native; otherwise
    # deterministic catalog order is already provider/channel sorted.
    for item in values:
        if item.provider == "tdx":
            return "tdx"
    return values[0].provider


def is_migrated_capability(capability: str) -> bool:
    return str(capability).strip().lower() in MIGRATED_CAPABILITIES
