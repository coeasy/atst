# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical Provider / Channel / Capability registry for the v11 runtime.

The registry is deliberately static and immutable. It answers *who* can provide
a capability and through *which* provider-internal channel. Runtime health,
retries, caching and fallback policy live elsewhere.

Provider identity is a trust boundary: selecting ``tdx`` must never silently
execute Tencent/Eastmoney/Sina. Host failover remains legal inside the selected
TDX Provider. Local vipdoc data is a separate Provider so historical files can
never impersonate live TDX network data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..errors import ValidationError

__all__ = [
    "ChannelSpec",
    "ProviderSpec",
    "ProviderRegistry",
    "PROVIDERS",
    "normalize_provider_id",
    "resolve_provider",
    "resolve_capability_provider",
]


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """Static execution facts for one provider-internal channel.

    No per-call quotas here: a batching ceiling belongs beside the protocol code
    that enforces it. No market claims either: market correctness fail-closes in
    ``Symbol.tdx_market``, and a second market vocabulary here would be enforced
    by nobody.
    """

    id: str
    capabilities: frozenset[str]
    live: bool = False
    local: bool = False
    periods: frozenset[str] = frozenset()
    unavailable_capabilities: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        id: str,
        capabilities: Iterable[str],
        *,
        live: bool = False,
        local: bool = False,
        periods: Iterable[str] = (),
        unavailable: Iterable[str] = (),
    ) -> ChannelSpec:
        channel_id = str(id).strip().lower()
        if not channel_id:
            raise ValueError("channel id must not be empty")
        caps = frozenset(str(x).strip().lower() for x in capabilities if str(x).strip())
        if not caps:
            raise ValueError(f"channel {channel_id!r} must declare at least one capability")

        normalized_periods = frozenset(str(x).strip().lower() for x in periods if str(x).strip())
        if normalized_periods and "bars" not in caps:
            raise ValueError(f"periods declared on non-bars channel {channel_id!r}")
        unavailable_caps = frozenset(
            str(x).strip().lower() for x in unavailable if str(x).strip()
        )
        unknown_unavailable = unavailable_caps - caps
        if unknown_unavailable:
            raise ValueError(
                f"channel {channel_id!r} marks undeclared capabilities unavailable: "
                f"{sorted(unknown_unavailable)}"
            )

        return cls(
            id=channel_id,
            capabilities=caps,
            live=bool(live),
            local=bool(local),
            periods=normalized_periods,
            unavailable_capabilities=unavailable_caps,
        )

    def supports_period(self, period: str) -> bool:
        return not self.periods or str(period).strip().lower() in self.periods

    def operationally_supports(self, capability: str) -> bool:
        cap = str(capability).strip().lower()
        return cap in self.capabilities and cap not in self.unavailable_capabilities


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Static facts for one independent data Provider."""

    id: str
    channels: tuple[ChannelSpec, ...]
    default: bool = False

    def __post_init__(self) -> None:
        ids = [item.id for item in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError(f"provider {self.id!r} has duplicate channel ids")

    def channel(self, channel_id: str) -> ChannelSpec:
        cid = str(channel_id).strip().lower()
        for item in self.channels:
            if item.id == cid:
                return item
        raise ValidationError(
            f"provider {self.id!r} 不存在 channel {channel_id!r}",
            context={"provider": self.id, "channel": channel_id},
        )

    def supports(self, capability: str, *, channel: str | None = None) -> bool:
        cap = str(capability).strip().lower()
        if channel is not None:
            return cap in self.channel(channel).capabilities
        return any(cap in item.capabilities for item in self.channels)

    def channels_for(self, capability: str) -> tuple[ChannelSpec, ...]:
        cap = str(capability).strip().lower()
        return tuple(item for item in self.channels if cap in item.capabilities)

    def available_channels_for(self, capability: str) -> tuple[ChannelSpec, ...]:
        return tuple(item for item in self.channels if item.operationally_supports(capability))

    def operationally_supports(self, capability: str) -> bool:
        return bool(self.available_channels_for(capability))

    def capabilities(self) -> frozenset[str]:
        return frozenset(cap for channel in self.channels for cap in channel.capabilities)


class ProviderRegistry:
    """Immutable single source of truth for Provider capabilities."""

    def __init__(self, specs: Iterable[ProviderSpec]) -> None:
        values = tuple(specs)
        by_id: dict[str, ProviderSpec] = {}
        for spec in values:
            pid = normalize_provider_id(spec.id)
            if pid in by_id:
                raise ValueError(f"duplicate provider id: {pid}")
            if pid != spec.id:
                raise ValueError(f"provider id {spec.id!r} is not canonical; expected {pid!r}")
            by_id[pid] = spec
        defaults = [spec.id for spec in values if spec.default]
        if len(defaults) != 1:
            raise ValueError(f"exactly one default provider is required, got {defaults!r}")
        self._providers: Mapping[str, ProviderSpec] = MappingProxyType(by_id)
        self._default = defaults[0]

    @property
    def default_provider(self) -> str:
        return self._default

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def get(self, provider: str) -> ProviderSpec:
        pid = normalize_provider_id(provider)
        try:
            return self._providers[pid]
        except KeyError as exc:
            raise ValidationError(
                f"未知 provider {provider!r}；可选: {sorted(self._providers)}",
                context={"provider": provider, "known_providers": sorted(self._providers)},
            ) from exc

    def supports(self, provider: str, capability: str, *, channel: str | None = None) -> bool:
        return self.get(provider).supports(capability, channel=channel)

    def require(
        self,
        provider: str,
        capability: str,
        *,
        channel: str | None = None,
    ) -> ProviderSpec:
        spec = self.get(provider)
        if not spec.supports(capability, channel=channel):
            raise ValidationError(
                f"provider {spec.id!r} 不支持 capability {capability!r}"
                + (f" on channel {channel!r}" if channel else ""),
                context={
                    "provider": spec.id,
                    "channel": channel,
                    "capability": str(capability).strip().lower(),
                },
            )
        return spec

    def available_providers(self, capability: str) -> tuple[str, ...]:
        cap = str(capability).strip().lower()
        return tuple(
            provider_id
            for provider_id, spec in self._providers.items()
            if spec.operationally_supports(cap)
        )

    def default_available_provider(self, capability: str) -> str | None:
        available = self.available_providers(capability)
        if self._default in available:
            return self._default
        return available[0] if available else None

    def require_period(self, provider: str, channel: str, period: str) -> ChannelSpec:
        spec = self.get(provider).channel(channel)
        if "bars" not in spec.capabilities:
            raise ValidationError(
                f"provider {provider!r} channel {channel!r} 不支持 bars",
                context={"provider": provider, "channel": channel, "capability": "bars"},
            )
        normalized = str(period).strip().lower()
        if not spec.supports_period(normalized):
            raise ValidationError(
                f"provider {provider!r} channel {channel!r} 不支持 period {period!r}",
                context={
                    "provider": normalize_provider_id(provider),
                    "channel": str(channel).strip().lower(),
                    "capability": "bars",
                    "period": normalized,
                    "supported_periods": sorted(spec.periods),
                },
            )
        return spec


_PROVIDER_ALIASES = {
    "qq": "tencent",
    "em": "eastmoney",
    "east_money": "eastmoney",
    "wencai": "iwencai",
    "local": "local_vipdoc",
    "reader": "local_vipdoc",
    "vipdoc": "local_vipdoc",
}


def normalize_provider_id(value: str) -> str:
    raw = str(value).strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(raw, raw)


def resolve_provider(
    *,
    provider: str | None = None,
    source: str | None = None,
    default: str | None = None,
) -> str:
    """Resolve compatibility selectors into exactly one Provider id.

    ``source='web'`` is intentionally *not* an alias: it is ambiguous now that
    Eastmoney/Tencent/Sina/JSL/BOC are independent Providers.
    """

    p = normalize_provider_id(provider) if provider is not None else None
    s = normalize_provider_id(source) if source is not None else None
    if p is not None and s is not None and p != s:
        raise ValidationError(
            f"provider={provider!r} 与 source={source!r} 指向不同 Provider",
            context={"provider": provider, "source": source},
        )
    selected = p or s or normalize_provider_id(default or "tdx")
    if selected == "web":
        raise ValidationError(
            "source/provider='web' 已是歧义选择器；请显式指定 eastmoney/tencent/sina 等 Provider",
            context={"provider": selected, "ambiguous": True},
        )
    return selected


def resolve_capability_provider(
    capability: str,
    provider: str | None = None,
    *,
    default: str | None = None,
) -> str:
    """Resolve omitted providers away from a declared-but-unavailable default.

    An explicit per-request provider is never replaced. For an omitted provider,
    the configured/default Provider keeps ownership when it is operational. Only
    when that Provider declares the capability but marks it operationally
    unavailable do we select the registry's operational default. If no operational
    Provider exists, the original default remains selected so its canonical
    fail-fast domain exception stays observable.
    """

    if provider is not None:
        return resolve_provider(provider=provider)
    candidate = resolve_provider(default=default or PROVIDERS.default_provider)
    spec = PROVIDERS.get(candidate)
    cap = str(capability).strip().lower()
    if not spec.supports(cap) or spec.operationally_supports(cap):
        return candidate
    selected = PROVIDERS.default_available_provider(cap)
    return selected or candidate

def _c(
    id: str,
    *capabilities: str,
    live: bool = False,
    local: bool = False,
    periods: Iterable[str] = (),
    unavailable: Iterable[str] = (),
) -> ChannelSpec:
    return ChannelSpec.build(
        id,
        capabilities,
        live=live,
        local=local,
        periods=periods,
        unavailable=unavailable,
    )


PROVIDERS = ProviderRegistry(
    (
        ProviderSpec(
            id="tdx",
            default=True,
            channels=(
                _c(
                    "quotation",
                    "quotes",
                    "bars",
                    "minute",
                    "trades",
                    "security_count",
                    "security_list",
                    "finance",
                    "capital_changes",
                    "corporate_action",
                    "snapshot",
                    "auction",
                    "block_quotes",
                    "minute_history",
                    "quotes_concurrent",
                    "security_list_all",
                    "volume_price",
                    live=True,
                    unavailable=("minute", "trades", "security_list", "security_list_all"),
                    periods=(
                        "1min",
                        "5min",
                        "15min",
                        "30min",
                        "60min",
                        "day",
                        "week",
                        "month",
                        "season",
                        "year",
                    ),
                ),
                _c(
                    "extended",
                    "quotes",
                    "bars",
                    "ex_market_list",
                    "ex_instruments",
                    "ex_quotes",
                    "ex_bars",
                    live=True,
                ),
                _c(
                    "goods",
                    "quotes",
                    "bars",
                    "goods_quotes",
                    "goods_bars",
                    live=True,
                ),
                _c("f10", "f10_catalog", "f10"),
                _c("mac", "quotes", "mac_quotes", live=True),
            ),
        ),
        ProviderSpec(
            id="local_vipdoc",
            channels=(
                _c(
                    "vipdoc",
                    "bars",
                    local=True,
                    periods=("1min", "5min", "day"),
                ),
            ),
        ),
        ProviderSpec(
            id="tencent",
            channels=(
                _c("quote", "quotes", live=True),
                _c(
                    "kline",
                    "bars",
                    periods=("day", "week", "month"),
                ),
                _c(
                    "minute_kline",
                    "bars",
                    periods=("1min", "5min", "15min", "30min", "60min"),
                ),
                _c("minute", "minute", live=True),
                _c("ticks", "trades", live=True),
                _c("global", "global_quotes", live=True),
                _c("market_stat", "market_stat", live=True),
                _c("board_rank", "board_rank", live=True),
                _c(
                    "catalog",
                    "all_market",
                    "globals",
                    "hk_quotes",
                    "minute_klines",
                    "minute_web",
                    "ticks",
                    "us_quotes",
                ),
            ),
        ),
        ProviderSpec(
            id="sina",
            channels=(
                _c("quote", "quotes", live=True),
                _c(
                    "history_kline",
                    "bars",
                    periods=("5min", "15min", "30min", "60min", "120min", "day", "1200min"),
                ),
                _c("suggest", "suggest"),
                _c("industry_board", "industry_board"),
                _c("board_list", "board_list"),
                _c("board_member", "board_member"),
                _c(
                    "fund_flow",
                    "fund_flow",
                ),
                _c("news", "news"),
                _c(
                    "catalog",
                    "all_market",
                    "board_members",
                    "esg_history",
                    "esg_rating",
                    "esg_ratings_all",
                    "history",
                    "hk_quotes",
                    "industry_boards",
                    "search_symbols",
                ),
            ),
        ),
        ProviderSpec(
            id="eastmoney",
            channels=(
                _c("quote", "quotes", live=True),
                _c(
                    "kline",
                    "bars",
                    periods=("1min", "5min", "15min", "30min", "60min", "day"),
                ),
                _c("trends", "minute", live=True),
                _c("rank", "rank"),
                _c("fund_flow", "fund_flow"),
                _c("limit_pool", "limit_pool", live=True),
                _c("stock_changes", "stock_changes", live=True),
                _c("northbound", "northbound", live=True),
                _c("hot_rank", "hot_rank", live=True),
                _c("corporate", "corporate_action"),
                _c("longhu", "longhu"),
                _c("margin", "margin"),
                _c("index_constituents", "index_constituents"),
                _c(
                    "fund",
                    "fund_base_info",
                    "fund_base_info_multi",
                    "fund_manager",
                    "fund_holdings",
                    "fund_rank",
                    "fund_period_change",
                    "fund_asset_allocation",
                    "fund_industry_distribution",
                    "fund_public_dates",
                ),
                _c(
                    "derivatives",
                    "futures_base_info",
                    "futures_realtime",
                    "futures_kline",
                    "futures_trades",
                    "bond_realtime",
                    "bond_base_info",
                    "bond_all_base_info",
                    "bond_kline",
                    "bond_history_bill",
                    "bond_today_bill",
                    "bond_trades",
                ),
                _c(
                    "datacenter",
                    "stock_base_info",
                    "stock_all_performance",
                    "stock_report_dates",
                    "ipo_review",
                    "dividend_history",
                    "stock_valuation",
                    "holder_changes",
                    "financial_abstract",
                    "balance_sheet",
                    "income_sheet",
                    "cash_flow",
                    "announcements",
                    "free_holders",
                    "holder_num",
                ),
                _c("news", "news_financial"),
                _c("research", "research_reports", "research_visits"),
                _c(
                    "options",
                    "options_list",
                    "options_snapshot",
                    "options_trends",
                    live=True,
                ),
                _c(
                    "catalog",
                    "em_board_members",
                    "em_boards",
                    "history",
                    "intraday",
                    "minute_klines",
                    "stock_boards",
                ),
            ),
        ),
        ProviderSpec(
            id="baidu",
            channels=(
                _c("quote", "quotes", live=True),
                _c(
                    "kline",
                    "bars",
                    periods=("day", "week", "month"),
                ),
                _c("minute", "minute", live=True),
                _c("ticks", "trades", live=True),
                _c("catalog", "baidu_kline", "baidu_minute", "baidu_quote", "baidu_ticks"),
            ),
        ),
        ProviderSpec(
            id="jsl",
            channels=(
                _c(
                    "bond",
                    "convertible_bond",
                ),
            ),
        ),
        ProviderSpec(
            id="boc",
            channels=(
                _c(
                    "fx",
                    "fx_rates",
                    "rates",
                ),
            ),
        ),
        ProviderSpec(
            id="iwencai",
            channels=(
                _c(
                    "screening",
                    "wencai",
                    "screening",
                ),
            ),
        ),
        # ``derived`` / ``builtin`` are *composite* Providers: their capabilities
        # are honest aggregates / static built-ins rather than one first-party
        # transport. They are declared in the registry so the migrated-capability
        # catalog has a canonical home, but they own no channel adapter (see
        # ``ChannelBindings.CHANNEL_API_EXEMPT``) — they are reachable only
        # through the unified QuerySpec path, never through a fake adapter.
        ProviderSpec(
            id="builtin",
            channels=(_c("catalog", "dc_reports", "index_list"),),
        ),
        ProviderSpec(
            id="derived",
            channels=(
                _c(
                    "catalog",
                    "all_market",
                    "announcements",
                    "balance_sheet",
                    "big_order_flow",
                    "block_trades",
                    "bond_all_base_info",
                    "bond_base_info",
                    "bond_history_bill",
                    "bond_kline",
                    "bond_realtime",
                    "bond_today_bill",
                    "bond_trades",
                    "cash_flow",
                    "chip_distribution",
                    "chip_distributions",
                    "concept_index",
                    "convertible_bonds",
                    "dc_query",
                    "dividend_history",
                    "earnings_preview",
                    "executive_holds",
                    "fin_report",
                    "financial_abstract",
                    "forecast",
                    "free_holders",
                    "fund_asset_allocation",
                    "fund_base_info",
                    "fund_base_info_multi",
                    "fund_companies",
                    "fund_company_archives",
                    "fund_company_base_info",
                    "fund_company_funds",
                    "fund_company_scale",
                    "fund_detail",
                    "fund_estimate",
                    "fund_flow",
                    "fund_flow_history",
                    "fund_holdings",
                    "fund_industry_distribution",
                    "fund_list",
                    "fund_manager",
                    "fund_manager_eval",
                    "fund_manager_list",
                    "fund_manager_profile",
                    "fund_manager_style",
                    "fund_manager_yield",
                    "fund_nav_history",
                    "fund_nav_history_mob",
                    "fund_period_change",
                    "fund_public_dates",
                    "fund_rank",
                    "fund_rank_trend",
                    "fund_rating",
                    "fund_search",
                    "fund_snapshot",
                    "fund_yield_curve",
                    "futures_base_info",
                    "futures_kline",
                    "futures_realtime",
                    "futures_trades",
                    "history",
                    "hk_quotes",
                    "holder_changes",
                    "holder_num",
                    "hot_boards",
                    "hot_rank",
                    "income_sheet",
                    "index",
                    "index_constituents",
                    "industry_index",
                    "ipo_calendar",
                    "ipo_review",
                    "klines",
                    "limit_pool",
                    "limit_up_ladder",
                    "longhu",
                    "macro_cpi",
                    "macro_gdp",
                    "macro_ppi",
                    "margin",
                    "market_breadth",
                    "news",
                    "news_financial",
                    "northbound",
                    "northbound_hold",
                    "notices",
                    "options_list",
                    "options_snapshot",
                    "options_trends",
                    "org_profile",
                    "org_profiles",
                    "performance",
                    "profile",
                    "rank",
                    "rating_consensus",
                    "rating_forecast",
                    "reports",
                    "research_reports",
                    "research_visits",
                    "sector_flow",
                    "shareholder_changes",
                    "shareholders",
                    "sina_board_fund_flow",
                    "sina_fund_flow",
                    "stock_all_performance",
                    "stock_base_info",
                    "stock_changes",
                    "stock_report_dates",
                    "stock_valuation",
                    "top_holders",
                    "unlock_stocks",
                    "unlocks",
                ),
                _c("adjustment", "adjusted_bars"),
                _c("sync", "sync_daily"),
            ),
        ),
    )
)
