# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 migrated capability catalog.

The retired UnifiedQuoteAPI is not restored. Every migrated business ability is
assigned to one explicit Provider/Channel and one low-level implementation.
Historical helpers that internally select or aggregate upstream services are
honestly represented by the explicit ``derived`` Provider.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType

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
    "us_quotes": "tencent",
    "globals": "tencent",
    "market_stat": "tencent",
    "ticks": "tencent",
    "intraday": "eastmoney",
    "em_boards": "eastmoney",
    "em_board_members": "eastmoney",
    "stock_boards": "eastmoney",
    "rates": "boc",
    "wencai": "iwencai",
    "index_list": "builtin",
    "dc_reports": "builtin",
}
_SOURCE_FOR_PROVIDER = {
    "derived": "sina",
    "eastmoney": "eastmoney",
    "sina": "sina",
    "tencent": "tencent",
    "baidu": "sina",
    "boc": "boc",
    "iwencai": "sina",
    "builtin": "sina",
}
_CORE_CAPABILITIES = {
    "quotes",
    "bars",
    "snapshot",
    "minute",
    "trades",
    "security_count",
    "security_list",
}
_SKIP_WEB_METHODS = {"close", "mro", *_CORE_CAPABILITIES}


def _discover_web_bindings() -> list[MigratedCapabilityBinding]:
    from .web.facade import WebQuoteSession

    values: list[MigratedCapabilityBinding] = []
    for name, _member in inspect.getmembers(WebQuoteSession, predicate=callable):
        if name.startswith("_") or name in _SKIP_WEB_METHODS:
            continue
        provider = _PROVIDER_OVERRIDES.get(name)
        if provider is None:
            provider = "baidu" if name.startswith("baidu_") else "derived"
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


_EXPLICIT_BINDINGS: tuple[MigratedCapabilityBinding, ...] = (
    MigratedCapabilityBinding(
        "quotes_concurrent", "tdx", "quotation_aux", "tdx_client", "quotes_concurrent"
    ),
    MigratedCapabilityBinding(
        "minute_history", "tdx", "quotation_aux", "tdx_client", "minute_history"
    ),
    MigratedCapabilityBinding(
        "block_quotes", "tdx", "quotation_aux", "tdx_client", "block_quotes"
    ),
    MigratedCapabilityBinding(
        "auction", "tdx", "quotation_aux", "tdx_client", "auction_snapshot"
    ),
    MigratedCapabilityBinding(
        "volume_price", "tdx", "quotation_aux", "tdx_client", "volume_price_dist"
    ),
    MigratedCapabilityBinding("finance", "tdx", "finance", "tdx_client", "finance_info"),
    MigratedCapabilityBinding(
        "capital_changes", "tdx", "finance", "tdx_client", "capital_changes"
    ),
    MigratedCapabilityBinding(
        "corporate_action", "tdx", "finance", "tdx_client", "capital_changes"
    ),
    MigratedCapabilityBinding(
        "security_list_all", "tdx", "quotation_aux", "composed", "security_list_all"
    ),
    MigratedCapabilityBinding("f10", "tdx", "f10", "f10_client", "f10"),
    MigratedCapabilityBinding("f10_catalog", "tdx", "f10", "f10_client", "catalog"),
    MigratedCapabilityBinding(
        "ex_market_list", "tdx", "extended", "ex_client", "ex_market_list"
    ),
    MigratedCapabilityBinding(
        "ex_instruments", "tdx", "extended", "ex_client", "ex_instrument_list"
    ),
    MigratedCapabilityBinding("ex_bars", "tdx", "extended", "ex_client", "ex_bars"),
    MigratedCapabilityBinding("ex_quotes", "tdx", "extended", "ex_client", "ex_quote"),
    MigratedCapabilityBinding("goods_bars", "tdx", "goods", "goods_client", "goods_bars"),
    MigratedCapabilityBinding(
        "goods_quotes", "tdx", "goods", "goods_client", "goods_quote"
    ),
    MigratedCapabilityBinding(
        "adjusted_bars", "derived", "adjustment", "composed", "adjusted_bars"
    ),
    MigratedCapabilityBinding("sync_daily", "derived", "sync", "composed", "sync_daily"),
    MigratedCapabilityBinding(
        "minute_web", "tencent", "catalog", "web_adapter", "minute_web"
    ),
    MigratedCapabilityBinding(
        "minute_klines", "tencent", "catalog", "web_adapter", "minute_klines"
    ),
    MigratedCapabilityBinding(
        "minute_klines", "eastmoney", "catalog", "web_adapter", "minute_klines"
    ),
    MigratedCapabilityBinding("history", "sina", "catalog", "web_adapter", "history"),
    MigratedCapabilityBinding(
        "history", "eastmoney", "catalog", "web_adapter", "history"
    ),
    MigratedCapabilityBinding(
        "all_market", "sina", "catalog", "web_session", "all_market", "sina"
    ),
    MigratedCapabilityBinding(
        "all_market", "tencent", "catalog", "web_session", "all_market", "tencent"
    ),
    MigratedCapabilityBinding(
        "hk_quotes", "sina", "catalog", "web_session", "hk_quotes", "sina"
    ),
    MigratedCapabilityBinding(
        "hk_quotes", "tencent", "catalog", "web_session", "hk_quotes", "tencent"
    ),
)


def _build_bindings() -> tuple[MigratedCapabilityBinding, ...]:
    by_key = {item.key: item for item in _discover_web_bindings()}
    for item in _EXPLICIT_BINDINGS:
        by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda x: x.key))


MIGRATED_BINDINGS = _build_bindings()
MIGRATED_CAPABILITIES = frozenset(item.capability for item in MIGRATED_BINDINGS)
_BINDINGS_BY_KEY = MappingProxyType({item.key: item for item in MIGRATED_BINDINGS})
_BINDINGS_BY_CAPABILITY = {
    capability: tuple(item for item in MIGRATED_BINDINGS if item.capability == capability)
    for capability in sorted(MIGRATED_CAPABILITIES)
}

_DEFAULT_PROVIDER = {
    "all_market": "derived",
    "hk_quotes": "derived",
    "history": "derived",
    "minute_klines": "derived",
}


def binding_for(provider: str, channel: str, capability: str) -> MigratedCapabilityBinding:
    return _BINDINGS_BY_KEY[(provider, channel, capability)]


def bindings_for_provider(provider: str) -> tuple[MigratedCapabilityBinding, ...]:
    pid = str(provider).strip().lower()
    return tuple(item for item in MIGRATED_BINDINGS if item.provider == pid)


def default_provider_for(capability: str) -> str:
    cap = str(capability).strip().lower()
    values = _BINDINGS_BY_CAPABILITY.get(cap)
    if not values:
        raise KeyError(capability)
    configured = _DEFAULT_PROVIDER.get(cap)
    if configured and any(item.provider == configured for item in values):
        return configured
    for preferred in (
        "derived",
        "tdx",
        "eastmoney",
        "sina",
        "tencent",
        "baidu",
        "boc",
        "iwencai",
        "builtin",
    ):
        for item in values:
            if item.provider == preferred:
                return preferred
    return values[0].provider


def is_migrated_capability(capability: str) -> bool:
    return str(capability).strip().lower() in MIGRATED_CAPABILITIES
