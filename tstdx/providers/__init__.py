# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 Provider / Channel / Capability registry.

The registry is executable truth. Core and migrated capabilities are declared
only when an exact DirectBinding exists. Provider identity is a trust boundary;
composite computations use the explicit ``derived`` Provider rather than
pretending to be a raw upstream source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..capability_catalog import MIGRATED_BINDINGS
from ..errors import ValidationError

__all__ = ["ChannelSpec", "ProviderSpec", "ProviderRegistry", "PROVIDERS", "normalize_provider_id", "resolve_provider"]


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    id: str
    capabilities: frozenset[str]
    markets: frozenset[str] = frozenset()
    live: bool = False
    local: bool = False
    notes: str = ""
    batch_limits: tuple[tuple[str, int], ...] = ()
    periods: frozenset[str] = frozenset()

    @classmethod
    def build(cls, id: str, capabilities: Iterable[str], *, markets: Iterable[str] = (), live: bool = False, local: bool = False, notes: str = "", batch_limits: Mapping[str, int] | None = None, periods: Iterable[str] = ()) -> "ChannelSpec":
        channel_id = str(id).strip().lower()
        if not channel_id:
            raise ValueError("channel id must not be empty")
        caps = frozenset(str(x).strip().lower() for x in capabilities if str(x).strip())
        if not caps:
            raise ValueError(f"channel {channel_id!r} must declare at least one capability")
        limits: list[tuple[str, int]] = []
        for capability, limit in dict(batch_limits or {}).items():
            cap = str(capability).strip().lower()
            if cap not in caps:
                raise ValueError(f"batch limit capability {cap!r} is not declared on channel {channel_id!r}")
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
                raise ValueError(f"batch limit for {cap!r} must be a positive int")
            limits.append((cap, limit))
        normalized_periods = frozenset(str(x).strip().lower() for x in periods if str(x).strip())
        if normalized_periods and "bars" not in caps:
            raise ValueError(f"periods declared on non-bars channel {channel_id!r}")
        return cls(channel_id, caps, frozenset(str(x).strip().lower() for x in markets if str(x).strip()), bool(live), bool(local), str(notes), tuple(sorted(limits)), normalized_periods)

    def batch_limit_for(self, capability: str) -> int | None:
        cap = str(capability).strip().lower()
        return next((limit for name, limit in self.batch_limits if name == cap), None)

    def supports_period(self, period: str) -> bool:
        return not self.periods or str(period).strip().lower() in self.periods


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    display_name: str
    role: str
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
        raise ValidationError(f"provider {self.id!r} 不存在 channel {channel_id!r}", context={"provider": self.id, "channel": channel_id})

    def supports(self, capability: str, *, channel: str | None = None) -> bool:
        cap = str(capability).strip().lower()
        return cap in self.channel(channel).capabilities if channel is not None else any(cap in item.capabilities for item in self.channels)

    def channels_for(self, capability: str) -> tuple[ChannelSpec, ...]:
        cap = str(capability).strip().lower()
        return tuple(item for item in self.channels if cap in item.capabilities)

    def capabilities(self) -> frozenset[str]:
        return frozenset(cap for channel in self.channels for cap in channel.capabilities)


class ProviderRegistry:
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
            raise ValidationError(f"未知 provider {provider!r}；可选: {sorted(self._providers)}", context={"provider": provider, "known_providers": sorted(self._providers)}) from exc

    def supports(self, provider: str, capability: str, *, channel: str | None = None) -> bool:
        return self.get(provider).supports(capability, channel=channel)

    def require(self, provider: str, capability: str, *, channel: str | None = None) -> ProviderSpec:
        spec = self.get(provider)
        if not spec.supports(capability, channel=channel):
            raise ValidationError(f"provider {spec.id!r} 不支持 capability {capability!r}" + (f" on channel {channel!r}" if channel else ""), context={"provider": spec.id, "channel": channel, "capability": str(capability).strip().lower()})
        return spec

    def require_period(self, provider: str, channel: str, period: str) -> ChannelSpec:
        spec = self.get(provider).channel(channel)
        if "bars" not in spec.capabilities:
            raise ValidationError(f"provider {provider!r} channel {channel!r} 不支持 bars", context={"provider": provider, "channel": channel, "capability": "bars"})
        normalized = str(period).strip().lower()
        if not spec.supports_period(normalized):
            raise ValidationError(f"provider {provider!r} channel {channel!r} 不支持 period {period!r}", context={"provider": normalize_provider_id(provider), "channel": str(channel).strip().lower(), "capability": "bars", "period": normalized, "supported_periods": sorted(spec.periods)})
        return spec


_PROVIDER_ALIASES = {"qq": "tencent", "em": "eastmoney", "east_money": "eastmoney", "local": "local_vipdoc", "reader": "local_vipdoc", "vipdoc": "local_vipdoc"}


def normalize_provider_id(value: str) -> str:
    raw = str(value).strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(raw, raw)


def resolve_provider(*, provider: str | None = None, default: str | None = None) -> str:
    selected = normalize_provider_id(provider or default or "tdx")
    if not selected:
        raise ValidationError("provider 不能为空")
    if selected == "web":
        raise ValidationError("'web' 不是 Provider；请显式指定具体 Provider", context={"provider": selected, "ambiguous": True})
    return selected


def _c(id: str, *capabilities: str, markets: Iterable[str] = (), live: bool = False, local: bool = False, notes: str = "", batch_limits: Mapping[str, int] | None = None, periods: Iterable[str] = ()) -> ChannelSpec:
    return ChannelSpec.build(id, capabilities, markets=markets, live=live, local=local, notes=notes, batch_limits=batch_limits, periods=periods)


def _migrated_channels(provider: str) -> tuple[ChannelSpec, ...]:
    grouped: dict[str, set[str]] = {}
    for item in MIGRATED_BINDINGS:
        if item.provider == provider:
            grouped.setdefault(item.channel, set()).add(item.capability)
    return tuple(_c(channel, *sorted(capabilities), notes="v13 migrated capability channel; exact DirectBinding required") for channel, capabilities in sorted(grouped.items()))


def _provider(id: str, display_name: str, role: str, core_channels: tuple[ChannelSpec, ...] = (), *, default: bool = False) -> ProviderSpec:
    return ProviderSpec(id=id, display_name=display_name, role=role, default=default, channels=core_channels + _migrated_channels(id))


PROVIDERS = ProviderRegistry((
    _provider("tdx", "TDX", "primary_live", (
        _c("quotation", "quotes", "bars", "snapshot", "minute", "trades", "security_count", "security_list", markets=("cn_a", "cn_bse"), live=True, batch_limits={"quotes": 60}, periods=("1min", "5min", "15min", "30min", "60min", "day", "week", "month", "season", "year"), notes="Canonical Tier-A Provider binding"),
    ), default=True),
    _provider("local_vipdoc", "Local TDX vipdoc", "local_historical", (
        _c("vipdoc", "bars", markets=("cn_a",), local=True, periods=("1min", "5min", "day"), notes="Explicit local historical Provider; never substitutes live TDX"),
    )),
    _provider("tencent", "Tencent Finance", "auxiliary", (
        _c("quote", "quotes", markets=("cn_a", "hk", "us"), live=True),
        _c("kline", "bars", markets=("cn_a", "hk", "us"), periods=("day", "week", "month")),
        _c("minute_kline", "bars", markets=("cn_a",), periods=("1min", "5min", "15min", "30min", "60min")),
    )),
    _provider("sina", "Sina Finance", "auxiliary", (
        _c("quote", "quotes", markets=("cn_a", "hk"), live=True),
        _c("history_kline", "bars", markets=("cn_a",), periods=("5min", "15min", "30min", "60min", "120min", "day", "1200min")),
    )),
    _provider("eastmoney", "Eastmoney", "auxiliary", (
        _c("quote", "quotes", markets=("cn_a",), live=True),
        _c("kline", "bars", markets=("cn_a", "hk", "us"), periods=("1min", "5min", "15min", "30min", "60min", "day")),
    )),
    _provider("baidu", "Baidu Finance", "auxiliary", (
        _c("quote", "quotes", markets=("cn_a",), live=True),
        _c("kline", "bars", markets=("cn_a",), periods=("day", "week", "month")),
    )),
    _provider("boc", "Bank of China", "reference_data"),
    _provider("iwencai", "iWencai", "screening"),
    _provider("builtin", "Built-in static catalog", "local_static"),
    _provider("derived", "Derived/composite runtime", "derived"),
))
