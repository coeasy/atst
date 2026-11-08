# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Provider / Channel / Capability 声明词汇层（v2 注册表收敛）。

本模块只定义**声明用的数据类型**与解析助手，不聚合任何具体 Provider：

* :class:`ChannelSpec` / :class:`ProviderSpec` —— Provider 的静态事实（channel、
  capability、周期、可用性）；
* :class:`CapabilityBinding` —— 一条 ``(provider, channel, capability) → 实现``
  的显式声明（v2 起取代 catalog 的 import 期反射发现）；
* :class:`ProviderRegistry` —— 聚合后的不可变注册表。

Provider 身份是信任边界：选 ``tdx`` 绝不会静默执行腾讯/东财/新浪；主机故障转移仍
合法地发生在选中的 TDX Provider 内部。本地 vipdoc 数据是独立 Provider，历史文件永远
不能冒充实时 TDX 网络数据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..errors import ValidationError

__all__ = [
    "CapabilityBinding",
    "ChannelSpec",
    "ProviderSpec",
    "ProviderRegistry",
    "_c",
    "normalize_provider_id",
    "resolve_capability_provider",
    "resolve_provider",
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
        unavailable_caps = frozenset(str(x).strip().lower() for x in unavailable if str(x).strip())
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


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """一条能力的显式执行声明：谁（provider）、走哪条内部通道（channel）、
    由哪个后端的哪个方法实现（backend/method）。

    v2 起每条绑定都在 :mod:`atst.providers` 的对应 Provider 声明模块里**逐条
    列出**，不再由 import 期反射 ``WebQuoteSession`` 成员自动发现——新增能力
    必须显式登记，登记缺失由启动对账（``audit_runtime``）当场红。
    """

    capability: str
    provider: str
    channel: str
    backend: str
    method: str
    source: str = ""
    #: ``web_adapter`` 后端的惰性导入路径，例如
    #: ``"atst.sources.tencent.adapters:MinuteSource"``。只有该后端读它；
    #: ``web_adapter`` 绑定缺 factory 是声明缺口，执行器报错而不是掩盖。
    factory: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


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


def _c(
    id: str,
    *capabilities: str,
    live: bool = False,
    local: bool = False,
    periods: Iterable[str] = (),
    unavailable: Iterable[str] = (),
) -> ChannelSpec:
    """Compact declaration helper used by every Provider declaration module."""

    return ChannelSpec.build(
        id,
        capabilities,
        live=live,
        local=local,
        periods=periods,
        unavailable=unavailable,
    )


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

    # 本函数在注册表聚合完成后才会被外部调用；这里取包级聚合是刻意的延迟读取。
    from . import PROVIDERS

    if provider is not None:
        return resolve_provider(provider=provider)
    candidate = resolve_provider(default=default or PROVIDERS.default_provider)
    spec = PROVIDERS.get(candidate)
    cap = str(capability).strip().lower()
    if not spec.supports(cap) or spec.operationally_supports(cap):
        return candidate
    selected = PROVIDERS.default_available_provider(cap)
    return selected or candidate
