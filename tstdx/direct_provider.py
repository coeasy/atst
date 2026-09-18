# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Exact Provider/Channel execution bindings for the canonical v13 runtime."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capability_audit import audit_capability_bindings
from .capability_catalog import binding_for, validate_call
from .errors import InternalError, TdxError, ValidationError
from .provider_audit import audit_provider_registry
from .provider_guard import ProviderExecutionIdentity, validate_execution_identity
from .runtime_audit import audit_runtime
from .providers import PROVIDERS
from .query import QueryPlan
from .result import Provenance, QueryResult

__all__ = [
    "DirectBinding",
    "DirectProviderExecutor",
    "DIRECT_BINDINGS",
    "audit_direct_bindings",
]


@dataclass(frozen=True, slots=True)
class DirectBinding:
    provider: str
    channel: str
    capability: str
    executor_name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


_CORE_BINDINGS: tuple[DirectBinding, ...] = (
    DirectBinding("tdx", "quotation", "quotes", "_tdx_quotes"),
    DirectBinding("tdx", "quotation", "bars", "_tdx_bars"),
    DirectBinding("tdx", "quotation", "snapshot", "_tdx_snapshot"),
    DirectBinding("tdx", "quotation", "minute", "_tdx_minute"),
    DirectBinding("tdx", "quotation", "trades", "_tdx_trades"),
    DirectBinding("tdx", "quotation", "security_count", "_tdx_security_count"),
    DirectBinding("tdx", "quotation", "security_list", "_tdx_security_list"),
    DirectBinding("local_vipdoc", "vipdoc", "bars", "_local_bars"),
    DirectBinding("tencent", "quote", "quotes", "_web_quotes"),
    DirectBinding("tencent", "kline", "bars", "_tencent_bars"),
    DirectBinding("tencent", "minute_kline", "bars", "_tencent_bars"),
    DirectBinding("sina", "quote", "quotes", "_web_quotes"),
    DirectBinding("sina", "history_kline", "bars", "_sina_bars"),
    DirectBinding("eastmoney", "quote", "quotes", "_web_quotes"),
    DirectBinding("eastmoney", "kline", "bars", "_eastmoney_bars"),
    DirectBinding("baidu", "quote", "quotes", "_web_quotes"),
    DirectBinding("baidu", "kline", "bars", "_baidu_bars"),
)

_CORE_EXECUTORS: dict[tuple[str, str, str], str] = {
    item.key: item.executor_name for item in _CORE_BINDINGS
}


def _executor_for(key: tuple[str, str, str]) -> str:
    return _CORE_EXECUTORS.get(key, "_migrated_capability")


def _registry_triples() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in sorted(channel.capabilities)
    )


DIRECT_BINDINGS = tuple(
    DirectBinding(provider, channel, capability, _executor_for((provider, channel, capability)))
    for provider, channel, capability in _registry_triples()
)


def _is_unified_reachable(provider: str, channel: str, capability: str) -> bool:
    from .query import _canonical_unified_channel

    if capability == "bars" and provider == "tencent":
        return channel in {"kline", "minute_kline"}
    canonical = _canonical_unified_channel(provider, capability, "")
    if canonical is not None:
        return channel == canonical
    owners = [item.id for item in PROVIDERS.get(provider).channels_for(capability)]
    return len(owners) == 1 and channel in owners


def audit_direct_bindings() -> tuple[DirectBinding, ...]:
    seen: set[tuple[str, str, str]] = set()
    unroutable: list[tuple[str, str, str]] = []
    for binding in DIRECT_BINDINGS:
        if binding.key in seen:
            raise RuntimeError(f"duplicate Direct binding: {binding.key!r}")
        seen.add(binding.key)
        if binding.executor_name != "_migrated_capability":
            continue
        if not _is_unified_reachable(*binding.key):
            continue
        try:
            binding_for(*binding.key)
        except KeyError:
            unroutable.append(binding.key)
    if unroutable:
        warnings.warn(
            f"{len(unroutable)} 个统一可达的 Direct binding 缺少 migrated-capability "
            f"执行元数据（例如 {unroutable[0]!r}）；运行时按 binding 表派发，"
            f"capability catalog 待补齐",
            stacklevel=2,
        )
    return DIRECT_BINDINGS


class DirectProviderExecutor:
    def __init__(
        self,
        *,
        timeout: float = 5.0,
        hosts: list[str] | None = None,
        vipdoc_root: str | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self.hosts = hosts
        self.vipdoc_root = vipdoc_root
        audit_runtime()
        self._bindings = {item.key: item for item in DIRECT_BINDINGS}
