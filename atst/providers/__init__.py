# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical Provider / Channel / Capability registry (v2).

Provider identity is a trust boundary: selecting ``tdx`` must never silently
execute Tencent/Eastmoney/Sina. Host failover remains legal inside the selected
TDX Provider. Local vipdoc data is a separate Provider so historical files can
never impersonate live TDX network data.

v2 起本包是**唯一手写声明处**：每个 Provider 一个声明模块
（``tdx.py`` / ``tencent.py`` …），本模块只做聚合与派生：

* :data:`PROVIDERS` —— 不可变 Provider 注册表；
* :data:`MIGRATED_BINDINGS` —— 每条 ``(provider, channel, capability) →
  实现`` 的显式绑定，取代 v1 catalog 的 import 期反射发现；
* :data:`CHANNEL_ADAPTERS` / :func:`resolve_channel_adapter` —— channel → adapter；
* 默认源、能力健康度、组合能力（composed）均从声明派生，不另立手写覆盖表。

校验（``validate_call`` / 三方对账）在 :mod:`atst.runtime`。
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from . import (
    baidu,
    boc,
    builtin,
    cninfo,
    derived,
    eastmoney,
    iwencai,
    jsl,
    local_vipdoc,
    sina,
    tdx,
    tencent,
    ths,
    wallstreet,
)
from .registry import (
    CapabilityBinding,
    ChannelSpec,
    ProviderRegistry,
    ProviderSpec,
    normalize_provider_id,
    resolve_capability_provider,
    resolve_provider,
)

__all__ = [
    "CAPABILITY_STATUSES",
    "CapabilityBinding",
    "ChannelSpec",
    "COMPOSED_CAPABILITIES",
    "MIGRATED_BINDINGS",
    "MIGRATED_CAPABILITIES",
    "MIGRATED_CAPABILITY_STATUS",
    "PROVIDERS",
    "ProviderRegistry",
    "ProviderSpec",
    "binding_for",
    "bindings_for_provider",
    "capability_status",
    "capability_statuses",
    "default_provider_for",
    "is_migrated_capability",
    "normalize_provider_id",
    "resolve_capability_provider",
    "resolve_channel_adapter",
    "resolve_provider",
]

#: 唯一声明顺序：注册表、绑定表、文档生成物均按此顺序稳定输出。
_DECLARATIONS = (
    tdx,
    local_vipdoc,
    tencent,
    sina,
    eastmoney,
    baidu,
    jsl,
    boc,
    iwencai,
    cninfo,
    ths,
    wallstreet,
    builtin,
    derived,
)

PROVIDERS = ProviderRegistry(tuple(module.SPEC for module in _DECLARATIONS))


def _validate_binding(item: CapabilityBinding) -> None:
    """声明期校验：``web_session`` 绑定必须显式登记来源，查表不许带默认值。

    ``.get(provider, "sina")`` 那种默认值会让一个新 Provider 的能力以别家的量纲单位
    上线。来源缺失在这里当场报，而不是让一次真实请求把错误数据当成功返回。
    """

    if item.backend == "web_session" and not item.source:
        raise KeyError(item.provider)


def _aggregate_bindings() -> tuple[CapabilityBinding, ...]:
    """拼装各 Provider 的显式绑定；重复三元组在聚合期即拒绝。"""

    by_key: dict[tuple[str, str, str], CapabilityBinding] = {}
    for module in _DECLARATIONS:
        provider_id = module.SPEC.id
        for item in module.BINDINGS:
            if item.provider != provider_id:
                raise ValueError(
                    f"provider {provider_id!r} 的声明模块登记了别家绑定：{item.provider!r}"
                )
            _validate_binding(item)
            if item.key in by_key:
                raise ValueError(f"duplicate capability binding: {item.key!r}")
            by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda item: item.key))


MIGRATED_BINDINGS = _aggregate_bindings()
MIGRATED_CAPABILITIES = frozenset(item.capability for item in MIGRATED_BINDINGS)

#: 走 ``composed`` 执行体的能力（``adjusted_bars`` / ``daily_enriched`` / ``sync_daily``）。
COMPOSED_CAPABILITIES: frozenset[str] = frozenset(
    item.capability for item in MIGRATED_BINDINGS if item.backend == "composed"
)

_BINDINGS_BY_KEY = MappingProxyType({item.key: item for item in MIGRATED_BINDINGS})
_BINDINGS_BY_CAPABILITY: dict[str, tuple[CapabilityBinding, ...]] = {
    capability: tuple(item for item in MIGRATED_BINDINGS if item.capability == capability)
    for capability in sorted(MIGRATED_CAPABILITIES)
}


def binding_for(provider: str, channel: str, capability: str) -> CapabilityBinding:
    return _BINDINGS_BY_KEY[(provider, channel, capability)]


def bindings_for_provider(provider: str) -> tuple[CapabilityBinding, ...]:
    pid = str(provider).strip().lower()
    return tuple(item for item in MIGRATED_BINDINGS if item.provider == pid)


def is_migrated_capability(capability: str) -> bool:
    return str(capability).strip().lower() in MIGRATED_CAPABILITIES


# ---------------------------------------------------------------------------
# Channel → adapter
# ---------------------------------------------------------------------------

_CHANNEL_DECLARATIONS: dict[str, Any] = {module.SPEC.id: module for module in _DECLARATIONS}


def _validate_channel_declarations() -> None:
    """声明期校验：注册表 channel 与 adapter 表必须一一对应。"""

    for provider_id, module in _CHANNEL_DECLARATIONS.items():
        if module.CHANNEL_API_EXEMPT:
            continue
        spec = PROVIDERS.get(provider_id)
        expected = {channel.id for channel in spec.channels if not channel.local}
        declared = (
            set(module.CHANNEL_ADAPTERS)
            | set(module.EXPLICIT_CHANNELS)
            | set(module.DIRECT_CHANNELS)
        )
        if declared != expected:
            missing = sorted(expected - declared)
            extra = sorted(declared - expected)
            raise RuntimeError(
                f"Channel declaration mismatch for Provider {provider_id!r}: "
                f"missing={missing} undeclared={extra}"
            )


_validate_channel_declarations()


def resolve_channel_adapter(provider: str, channel: str) -> type[Any]:
    """Return the adapter class bound to one Provider channel."""

    import importlib

    from ..errors import ValidationError

    pid = resolve_provider(provider=provider)
    cid = str(channel).strip().lower()
    adapters = _CHANNEL_DECLARATIONS[pid].CHANNEL_ADAPTERS
    try:
        module_name, class_name = adapters[cid]
    except KeyError as exc:
        raise ValidationError(
            f"Provider {pid!r} 的 channel {cid!r} 没有 adapter 引用",
            context={
                "provider": pid,
                "channel": cid,
                "phase": "channel_binding",
                "fallback": False,
                "provider_switch_allowed": False,
            },
        ) from exc
    return getattr(importlib.import_module(module_name), class_name)


# ---------------------------------------------------------------------------
# 默认源偏好（从声明派生，取代 v1 的三张手工覆盖表）
# ---------------------------------------------------------------------------

#: 能力默认源的兜底优先级；声明里 ``DEFAULT_CAPABILITIES`` 显式指名的能力优先。
_DEFAULT_PREFERENCE: tuple[str, ...] = (
    "derived",
    "tdx",
    "eastmoney",
    "sina",
    "tencent",
    "baidu",
    "boc",
    "iwencai",
    "builtin",
)

_DEFAULT_PROVIDER_CAPABILITIES: dict[str, str] = {}
_MARKET_DEFAULT_CAPABILITIES: dict[str, dict[str, str]] = {}
for _module in _DECLARATIONS:
    for _capability in _module.DEFAULT_CAPABILITIES:
        owners = {item.provider for item in _BINDINGS_BY_CAPABILITY.get(_capability, ())}
        if _module.SPEC.id not in owners:
            raise ValueError(
                f"provider {_module.SPEC.id!r} 声称默认 {_capability!r}，但没有该能力的绑定"
            )
        existing = _DEFAULT_PROVIDER_CAPABILITIES.get(_capability)
        if existing and existing != _module.SPEC.id:
            raise ValueError(
                f"capability {_capability!r} 有两个默认 Provider：{existing!r} / "
                f"{_module.SPEC.id!r}"
            )
        _DEFAULT_PROVIDER_CAPABILITIES[_capability] = _module.SPEC.id
    for _capability, _markets in _module.MARKET_DEFAULT_CAPABILITIES.items():
        owners = {item.provider for item in _BINDINGS_BY_CAPABILITY.get(_capability, ())}
        if _module.SPEC.id not in owners:
            raise ValueError(
                f"provider {_module.SPEC.id!r} 声称按市场默认 {_capability!r}，但没有该能力的绑定"
            )
        _MARKET_DEFAULT_CAPABILITIES.setdefault(_capability, {}).update(
            {market: _module.SPEC.id for market in _markets}
        )


def _symbol_market(symbol: str) -> str:
    """符号 → 市场（``sh``/``sz``/``bj``/``hk``/``us``）；解析不了返回空串。"""
    try:
        from ..domain.symbol import parse_symbol

        return parse_symbol(symbol).market
    except Exception:  # noqa: BLE001 - 非法符号留给下游 validate_call 报原话
        return ""


def default_provider_for(capability: str, symbol: str | None = None) -> str:
    """默认 Provider；``symbol`` 只影响按市场分叉的能力（目前 ``minute_klines``）。

    A 股默认腾讯 mkline，港股/美股默认东财 push2his；其它能力忽略 ``symbol``。
    """

    cap = str(capability).strip().lower()
    values = _BINDINGS_BY_CAPABILITY.get(cap)
    if not values:
        raise KeyError(capability)
    if symbol:
        market_owner = _MARKET_DEFAULT_CAPABILITIES.get(cap, {}).get(_symbol_market(symbol))
        if market_owner and any(item.provider == market_owner for item in values):
            return market_owner
    configured = _DEFAULT_PROVIDER_CAPABILITIES.get(cap)
    if configured and any(item.provider == configured for item in values):
        return configured
    for preferred in _DEFAULT_PREFERENCE:
        for item in values:
            if item.provider == preferred:
                return preferred
    return values[0].provider


# ---------------------------------------------------------------------------
# 能力健康度（显式叠加 + registry 派生）
# ---------------------------------------------------------------------------

#: 状态枚举（用于校验与文档生成）。
CAPABILITY_STATUSES = ("alive", "degraded", "offline", "needs_verify")

MIGRATED_CAPABILITY_STATUS: dict[str, str] = {}
for _module in _DECLARATIONS:
    for _capability, _status in _module.STATUS.items():
        if _status not in CAPABILITY_STATUSES:
            raise ValueError(
                f"迁移能力 {_capability!r} 的状态 {_status!r} 不在 {CAPABILITY_STATUSES} 之内"
            )
        existing = MIGRATED_CAPABILITY_STATUS.get(_capability)
        if existing and existing != _status:
            raise ValueError(f"迁移能力 {_capability!r} 有两个状态：{existing!r} / {_status!r}")
        MIGRATED_CAPABILITY_STATUS[_capability] = _status


@functools.lru_cache(maxsize=1)
def _registry_offline_capabilities() -> frozenset[str]:
    """声明了、但**每个**归属 channel 都标 ``unavailable`` 的能力。"""

    unavailable_by_cap: dict[str, list[bool]] = {}
    for pid in PROVIDERS.ids():
        for channel in PROVIDERS.get(pid).channels:
            for cap in channel.capabilities:
                unavailable_by_cap.setdefault(cap, []).append(
                    cap in channel.unavailable_capabilities
                )
    return frozenset(cap for cap, flags in unavailable_by_cap.items() if flags and all(flags))


def capability_status(capability: str) -> str:
    """返回单个迁移能力的状态；能力不存在抛 ``KeyError``。"""

    cap = str(capability).strip().lower()
    if cap not in MIGRATED_CAPABILITIES:
        raise KeyError(capability)
    status = MIGRATED_CAPABILITY_STATUS.get(cap)
    if status is None:
        status = "offline" if cap in _registry_offline_capabilities() else "alive"
    if status not in CAPABILITY_STATUSES:
        raise ValueError(f"迁移能力 {cap!r} 的状态 {status!r} 不在 {CAPABILITY_STATUSES} 之内")
    return status


def capability_statuses() -> dict[str, str]:
    """返回 ``{capability: status}``，覆盖全部迁移能力（默认 ``alive``）。"""
    return {cap: capability_status(cap) for cap in sorted(MIGRATED_CAPABILITIES)}


def preference_order() -> tuple[str, ...]:
    """全局 Provider 偏好序的唯一读数（P1-8）。

    sources 层的默认降级顺序等下游顺序账本从它派生，不再各自手抄一份——
    过去 ``(tencent, sina, eastmoney)`` 与这里 ``(eastmoney, sina, tencent,
    ...)`` 是两本没人对账的账（顺序账本禁令见
    ``tests/architecture/test_official_runtime_no_fallback.py``）。
    """
    return _DEFAULT_PREFERENCE


#: 供文档生成与测试读取的只读视图。
CHANNEL_ADAPTERS: Mapping[str, Mapping[str, tuple[str, str]]] = MappingProxyType(
    {module.SPEC.id: MappingProxyType(dict(module.CHANNEL_ADAPTERS)) for module in _DECLARATIONS}
)
