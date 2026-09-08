# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider / Channel / Capability canonical registry.

The v12 architecture has exactly one domain entity for "who provides data":
:class:`ProviderSpec`. Public ``source=`` remains a compatibility selector only;
it is normalized to the same provider id and is never stored as a second object.

Provider boundaries are also failure boundaries: selecting ``tdx`` must never
silently execute ``sina`` / ``tencent`` / ``eastmoney``. Host/endpoint failover
is allowed only inside the selected provider and channel.
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
]


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One provider-internal data channel with static execution facts.

    ``batch_limits`` and ``periods`` are capability-specific execution facts.
    New fields are appended after historical fields so positional construction
    remains source-compatible with older callers.
    """

    id: str
    capabilities: frozenset[str]
    markets: frozenset[str] = frozenset()
    live: bool = False
    local: bool = False
    notes: str = ""
    batch_limits: tuple[tuple[str, int], ...] = ()
    periods: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        id: str,
        capabilities: Iterable[str],
        *,
        markets: Iterable[str] = (),
        live: bool = False,
        local: bool = False,
        notes: str = "",
        batch_limits: Mapping[str, int] | None = None,
        periods: Iterable[str] = (),
    ) -> "ChannelSpec":
        normalized_caps = frozenset(str(x).strip().lower() for x in capabilities)
        limits: list[tuple[str, int]] = []
        for capability, limit in dict(batch_limits or {}).items():
            cap = str(capability).strip().lower()
            if cap not in normalized_caps:
                raise ValueError(
                    f"batch limit capability {cap!r} is not declared on channel {id!r}"
                )
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                raise ValueError(
                    f"batch limit for capability {cap!r} must be a positive int"
                )
            limits.append((cap, limit))
        limits.sort()
        normalized_periods = frozenset(str(x).strip().lower() for x in periods if str(x).strip())
        if normalized_periods and "bars" not in normalized_caps:
            raise ValueError(f"periods declared on non-bars channel {id!r}")
        return cls(
            id=str(id).strip().lower(),
            capabilities=normalized_caps,
            markets=frozenset(str(x).strip().lower() for x in markets),
            live=bool(live),
            local=bool(local),
            notes=notes,
            batch_limits=tuple(limits),
            periods=normalized_periods,
        )

    def batch_limit_for(self, capability: str) -> int | None:
        cap = str(capability).strip().lower()
        for name, limit in self.batch_limits:
            if name == cap:
                return limit
        return None

    def supports_period(self, period: str) -> bool:
        """Return whether a bars period is explicitly supported.

        Empty ``periods`` means this channel has no period contract because it is
        not a unified bars channel or its low-level surface remains provider-specific.
        """
        return not self.periods or str(period).strip().lower() in self.periods


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Static facts for one independent upstream provider."""

    id: str
    display_name: str
    role: str
    channels: tuple[ChannelSpec, ...]
    default: bool = False

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


class ProviderRegistry:
    """Immutable provider registry used by planning, docs and conformance tests."""

    def __init__(self, specs: Iterable[ProviderSpec]) -> None:
        values = tuple(specs)
        by_id: dict[str, ProviderSpec] = {}
        for spec in values:
            pid = normalize_provider_id(spec.id)
            if pid in by_id:
                raise ValueError(f"duplicate provider id: {pid}")
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
                    "capability": capability,
                },
            )
        return spec

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
    "iwencai": "iwencai",
    "wencai": "iwencai",
}


def normalize_provider_id(value: str) -> str:
    """Normalize a public provider/source selector to one canonical ProviderId."""
    raw = str(value).strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(raw, raw)


def resolve_provider(
    *,
    provider: str | None = None,
    source: str | None = None,
    default: str | None = None,
) -> str:
    """Resolve public selectors into exactly one Provider id."""
    p = normalize_provider_id(provider) if provider is not None else None
    s = normalize_provider_id(source) if source is not None else None
    if p is not None and s is not None and p != s:
        raise ValidationError(
            f"provider={provider!r} 与 source={source!r} 指向不同 Provider",
            context={"provider": provider, "source": source},
        )
    return p or s or normalize_provider_id(default or "tdx")


def _c(
    id: str,
    *capabilities: str,
    markets: Iterable[str] = (),
    live: bool = False,
    local: bool = False,
    notes: str = "",
    batch_limits: Mapping[str, int] | None = None,
    periods: Iterable[str] = (),
) -> ChannelSpec:
    return ChannelSpec.build(
        id,
        capabilities,
        markets=markets,
        live=live,
        local=local,
        notes=notes,
        batch_limits=batch_limits,
        periods=periods,
    )


PROVIDERS = ProviderRegistry(
    (
        ProviderSpec(
            id="tdx",
            display_name="TDX",
            role="primary_live",
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
                    "snapshot",
                    markets=("cn_a", "cn_bse"),
                    live=True,
                    notes="0x0530 realtime quote batch limit is 60 symbols",
                    batch_limits={"quotes": 60},
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
                _c("extended", "markets", "instruments", "quotes", "bars", live=True),
                _c("goods", "quotes", "bars", markets=("future", "commodity"), live=True),
                _c("f10", "f10_catalog", "f10_download"),
                _c("mac", "quotes", live=True),
                _c(
                    "vipdoc",
                    "bars",
                    markets=("cn_a", "future"),
                    local=True,
                    notes="local historical only; never substitutes live TDX data",
                    periods=("1min", "5min", "day"),
                ),
            ),
        ),
        ProviderSpec(
            id="tencent",
            display_name="Tencent Finance",
            role="auxiliary_live",
            channels=(
                _c("quote", "quotes", markets=("cn_a", "hk", "us"), live=True),
                _c(
                    "kline",
                    "bars",
                    markets=("cn_a", "hk", "us"),
                    periods=("day", "week", "month"),
                ),
                _c(
                    "minute_kline",
                    "bars",
                    markets=("cn_a",),
                    periods=("1min", "5min", "15min", "30min", "60min"),
                ),
                _c("minute", "minute", markets=("cn_a",), live=True),
                _c("ticks", "trades", markets=("cn_a",), live=True),
                _c("global", "global_quotes", live=True),
                _c("market_stat", "market_stat", live=True),
                _c("board_rank", "board_rank", markets=("cn_a",), live=True),
            ),
        ),
        ProviderSpec(
            id="sina",
            display_name="Sina Finance",
            role="auxiliary_live_info",
            channels=(
                _c("quote", "quotes", markets=("cn_a", "hk"), live=True),
                _c(
                    "history_kline",
                    "bars",
                    markets=("cn_a",),
                    periods=("5min", "15min", "30min", "60min", "120min", "day", "1200min"),
                ),
                _c("suggest", "suggest"),
                _c("industry_board", "industry_board"),
                _c("board_list", "board_list"),
                _c("board_member", "board_member"),
                _c("fund_flow", "fund_flow", markets=("cn_a",)),
                _c("news", "news"),
            ),
        ),
        ProviderSpec(
            id="eastmoney",
            display_name="Eastmoney",
            role="auxiliary_live_info",
            channels=(
                _c("quote", "quotes", markets=("cn_a",), live=True),
                _c(
                    "kline",
                    "bars",
                    markets=("cn_a", "hk", "us"),
                    periods=("1min", "5min", "15min", "30min", "60min", "day"),
                ),
                _c("trends", "minute", markets=("cn_a",), live=True),
                _c("rank", "rank"),
                _c("fund_flow", "fund_flow"),
                _c("limit_pool", "limit_pool", live=True),
                _c("stock_changes", "stock_changes", live=True),
                _c("northbound", "northbound", live=True),
                _c("hot_rank", "hot_rank", live=True),
                _c("corporate", "corporate"),
                _c("longhu", "longhu"),
                _c("margin", "margin"),
                _c("index_constituents", "index_constituents"),
                _c("fund", "fund"),
            ),
        ),
        ProviderSpec(
            id="baidu",
            display_name="Baidu Finance",
            role="auxiliary_live",
            channels=(
                _c("quote", "quotes", markets=("cn_a",), live=True),
                _c(
                    "kline",
                    "bars",
                    markets=("cn_a",),
                    periods=("day", "week", "month"),
                ),
                _c("minute", "minute", markets=("cn_a",), live=True),
                _c("ticks", "trades", markets=("cn_a",), live=True),
            ),
        ),
        ProviderSpec(
            id="jsl",
            display_name="Jisilu",
            role="auxiliary_info",
            channels=(
                _c(
                    "bond",
                    "bond",
                    markets=("bond",),
                    notes="verified cbnew convertible-bond endpoint; ETF is not registered",
                ),
            ),
        ),
        ProviderSpec(
            id="boc",
            display_name="Bank of China",
            role="auxiliary_info",
            channels=(_c("fx", "fx_rates", markets=("fx",)),),
        ),
        ProviderSpec(
            id="iwencai",
            display_name="iWencai",
            role="auxiliary_info",
            channels=(_c("screening", "screening", markets=("cn_a",)),),
        ),
    )
)