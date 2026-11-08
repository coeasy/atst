# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Direct binding projection: ``(provider, channel, capability) -> executor``.

本模块只回答“这一格由谁来执行”，不做 I/O。执行体本体在
``executor.py`` / ``execute_*.py``；两者都从 :data:`DIRECT_BINDINGS` 读同一张表。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..providers import PROVIDERS, binding_for


@dataclass(frozen=True, slots=True)
class DirectBinding:
    provider: str
    channel: str
    capability: str
    executor_name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


_WEB_DEDICATED_PROVIDERS = frozenset({"tencent", "sina", "eastmoney", "baidu"})


def _executor_for(provider: str, channel: str, capability: str) -> str:
    """Determine which executor method handles a (provider, channel, capability) triple.

    Rules cover all 17 dedicated bindings:
      - ``tdx/quotation`` → ``_tdx_<capability>`` for the 7 dedicated quote-capabilities
      - ``local_vipdoc/vipdoc/bars`` → ``_local_bars``
      - web providers' quote channel → ``_web_quotes``
      - web providers' kline/history_kline/minute_kline channels → ``_<provider>_bars``
    Everything else falls through to ``_migrated_capability``.
    """
    if provider == "tdx" and channel == "quotation":
        if capability in {
            "quotes",
            "bars",
            "snapshot",
            "minute",
            "trades",
            "security_count",
            "security_list",
        }:
            return f"_tdx_{capability}"
    elif provider == "local_vipdoc" and channel == "vipdoc":
        if capability == "bars":
            return "_local_bars"
    elif provider in _WEB_DEDICATED_PROVIDERS:
        if capability == "quotes" and channel == "quote":
            return "_web_quotes"
        if capability == "bars" and channel in {"kline", "history_kline", "minute_kline"}:
            return f"_{provider}_bars"
    return "_migrated_capability"


def _registry_triples() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in sorted(channel.capabilities)
    )


DIRECT_BINDINGS = tuple(
    DirectBinding(provider, channel, capability, _executor_for(provider, channel, capability))
    for provider, channel, capability in _registry_triples()
)


def _is_unified_reachable(provider: str, channel: str, capability: str) -> bool:
    """这个三元组会不会真的被统一执行面派发。

    判据只取注册表自己的数据：``bars`` 的 canonical channel 随周期分家，而这条
    事实的唯一持有者是 :func:`atst.query._canonical_unified_channel`。所以"可达"
    = 这个 channel **声明的任一周期** 的 canonical 就是它本身。早先这里写死过
    ``tencent → {kline, minute_kline}``，等于把同一个业务事实在第二个地方抄一遍
    （F-27/F-49 那一族"影子规则"的现存样本）。
    """
    from ..query import _canonical_unified_channel

    declared = next((item for item in PROVIDERS.get(provider).channels if item.id == channel), None)
    periods = sorted(declared.periods) if declared is not None and declared.periods else [""]
    canonical = {_canonical_unified_channel(provider, capability, period) for period in periods}
    if channel in canonical:
        return True
    if canonical != {None}:
        return False
    owners = [item.id for item in PROVIDERS.get(provider).channels_for(capability)]
    return len(owners) == 1 and channel in owners


def audit_direct_bindings() -> tuple[DirectBinding, ...]:
    """绑定表自查：重复即红，"统一可达却没有执行元数据"也即红。

    后者原先只 ``warnings.warn``（同文件的注册表对账却是 raise）。一软一硬的差别
    不是分寸而是成败：pytest 收集告警却不因它失败，于是这条缺陷在 CI 上永远看不见，
    只有读日志的人能发现。判据既然写得出来，就该按它决定要不要放行。
    """
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
        raise RuntimeError(
            f"{len(unroutable)} 个统一可达的 Direct binding 缺少 migrated-capability "
            f"执行元数据（例如 {unroutable[0]!r}）：运行时按 binding 表派发，"
            f"capability catalog 却不认这个三元组，两侧必须补齐而不是带着缺口上线"
        )
    return DIRECT_BINDINGS


DEDICATED_CAPABILITIES: frozenset[str] = frozenset(
    binding.capability
    for binding in DIRECT_BINDINGS
    if binding.executor_name != "_migrated_capability"
)
