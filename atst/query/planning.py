# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Deterministic single-Provider planning: compile a QuerySpec into a QueryPlan.

P2-1：从原 ``atst/query.py`` 拆出的规划面——canonical channel 选择、auto 解析、
plan 编译与预算装载。跨 Provider fallback 是 orchestration 政策， planner 内永不发生。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ..domain.period import MINUTE_PERIODS
from ..errors import ValidationError
from ..providers import PROVIDERS, ChannelSpec, resolve_provider
from .fingerprint import QueryFingerprint
from .spec import (
    CurrentnessMode,
    ExecutionBudget,
    QuerySpec,
    _parse_currentness,
)

__all__ = ["QueryPlan", "QueryPlanner"]


#: 港美股等 TDX 二进制不可服务市场的 bars 缺省候选，按偏好排序。只收 Web Provider：
#: eastmoney 支持 qfq/hfq 且返回成交额；tencent 成交额为 0（告警）。tdx/baidu/sina
#: 在真机上对 hk/us 分别抛 E4040/E7000/E7030，不能作为缺省。
_NON_TDX_BAR_PROVIDERS: tuple[str, ...] = ("eastmoney", "tencent")

#: 非沪深标的（BJ / HK / US / 混合代码）的 quotes 缺省候选，按真机可用性排序。
#: tencent 的 canonical ``quote`` 通道 live=True 且一份请求可同时覆盖 A 股、
#: BJ、港股与美股（2026-11-24 本地真机实测）；eastmoney 的 push2 三节点在本机
#: 被 WAF 断连，列为次选而非首选。tdx 二进制对这批代码不可用，一律不进候选。
_NON_TDX_QUOTE_PROVIDERS: tuple[str, ...] = ("tencent", "eastmoney")

_CANONICAL_UNIFIED_CHANNELS: dict[tuple[str, str], str] = {
    ("tdx", "quotes"): "quotation",
    ("tdx", "bars"): "quotation",
    ("tencent", "quotes"): "quote",
    ("sina", "quotes"): "quote",
    ("eastmoney", "quotes"): "quote",
    ("baidu", "quotes"): "quote",
    ("sina", "bars"): "history_kline",
    ("eastmoney", "bars"): "kline",
    ("baidu", "bars"): "kline",
    ("local_vipdoc", "bars"): "vipdoc",
}


def _canonical_unified_channel(provider: str, capability: str, period: str) -> str | None:
    """Return the single channel the unified ``quotes``/``bars`` executor uses.

    ``None`` means the (provider, capability) pair has no unified canonical
    channel, so an explicit provider-internal channel must be honored verbatim.
    """

    pid = str(provider)
    cap = str(capability)
    if pid == "tencent" and cap == "bars":
        return "minute_kline" if period in MINUTE_PERIODS else "kline"
    return _CANONICAL_UNIFIED_CHANNELS.get((pid, cap))


def _reject_core_channel_mismatch(
    provider: str,
    capability: str,
    channel: str | None,
    canonical: str | None,
) -> None:
    """Fail fast when a unified core query targets a provider-specific channel.

    ``quotes`` / ``bars`` are unified capabilities: exactly one canonical channel
    per Provider executes them. Any other declared channel (``extended``,
    ``goods``, ``vipdoc`` …) must never be silently re-routed through the
    canonical executor, so the mismatch is rejected *before* registry lookup.
    """

    if channel is None or canonical is None or channel == canonical:
        return
    if capability not in {"quotes", "bars"}:
        return
    raise ValidationError(
        f"统一 {capability} QueryPlan 不能执行 provider {provider!r} "
        f"channel {channel!r}；canonical channel 为 {canonical!r}",
        context={
            "provider": provider,
            "channel": channel,
            "canonical_channel": canonical,
            "capability": capability,
            "provider_switch_allowed": False,
            "channel_switch_allowed": False,
        },
    )


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Compiled executable identity. It can never contain fallback Providers."""

    spec: QuerySpec
    provider: str
    channel: str
    fingerprint: QueryFingerprint
    #: 单次逻辑查询共享的总 deadline 预算。属于**运行时状态**而非编译身份，
    #: 因此刻意排除在相等性与哈希之外（两个独立编译、语义相同的 plan 仍相等）。
    #: 它是 deadline 的唯一载体——`deadline_ms` 曾经另存一份，无人读取（F-50）。
    budget: ExecutionBudget = field(compare=False, repr=False)
    #: P1-6：调用方请求了 ``auto`` 时记录在 plan 上，Provenance 才能如实反映
    #: "requested='auto'，实际由某家出数"，而不是与钉死 provider 的请求无法区分。
    #: 属于运行时事实而非编译身份，与 budget 同口径排除在相等性之外。
    requested_provider: str | None = field(default=None, compare=False, repr=False)


class QueryPlanner:
    """Compile semantic requests into deterministic one-Provider plans."""

    def __init__(
        self,
        *,
        default_provider: str | None = None,
        auto_prefer: Sequence[str] = (),
        auto_ttl_ns: int = 0,
        auto_exclude: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        if default_provider is None:
            selected: str = PROVIDERS.default_provider
        else:
            # v13 SSOT：显式传入的 default_provider 是调用方的意图声明，空串必须
            # fail-fast，绝不能静默回落成内置默认 Provider（否则配置拼写错误会被吞掉）。
            cleaned = str(default_provider).strip()
            if not cleaned:
                raise ValidationError(
                    "default_provider 不能为空；如需内置默认 Provider 请传 None",
                    context={"default_provider": default_provider},
                )
            selected = cleaned
        #: ``auto`` 是显式自动选源的策略名（:mod:`atst.runtime.autoselect`）：
        #: 配成缺省后，未指定 provider 的每次请求都走能力面 + 健康反馈选源。
        #: 它不是一个 Provider，注册表查不到它是正常的。
        from ..runtime.autoselect import AUTO_PROVIDER

        if selected.lower() != AUTO_PROVIDER:
            PROVIDERS.get(selected)
        self.default_provider = resolve_provider(provider=selected)
        #: 自动选源的候选偏好与健康反馈窗口（构造期取自配置；``auto`` 之外的
        #: 路径不读它们，空值即关闭）。
        self.auto_prefer = tuple(
            str(item).strip().lower() for item in auto_prefer if str(item).strip()
        )
        self.auto_ttl_ns = int(auto_ttl_ns)
        self.auto_exclude = frozenset(auto_exclude)

    def _resolve_auto(self, spec: QuerySpec) -> QuerySpec:
        """把 ``auto`` 解析成一个具体 Provider（静态选源，单跳执行）。

        两种显式形态：请求写了 ``provider='auto'``，或缺省 Provider 就是
        ``auto`` 且本次没钉死 Provider。跨源重试是 Client 侧显式编排的职责
        （:meth:`atst.client.api.Client._execute_auto`）；内核只做静态一跳。
        """
        from ..runtime.autoselect import (
            is_auto_provider,
            resolve_auto_provider,
        )

        # P1-6：``local_vipdoc`` 的排除由内核按 ``vipdoc_root`` 配置下沉到这里
        # （planner 自持 auto_exclude），不再只靠 Client 层一处特判——同是
        # ``provider="auto"``，直连内核与走 Client 的候选面必须一致。
        if is_auto_provider(spec.provider):
            return replace(
                spec,
                provider=resolve_auto_provider(
                    spec.capability,
                    prefer=self.auto_prefer,
                    default=PROVIDERS.default_provider,
                    ttl_ns=self.auto_ttl_ns,
                    exclude=self.auto_exclude,
                ),
            )
        if spec.provider is None and is_auto_provider(self.default_provider):
            return replace(
                spec,
                provider=resolve_auto_provider(
                    spec.capability,
                    prefer=self.auto_prefer,
                    default=PROVIDERS.default_provider,
                    ttl_ns=self.auto_ttl_ns,
                    exclude=self.auto_exclude,
                ),
            )
        return spec

    @staticmethod
    def _canonical_unified_channel(spec: QuerySpec) -> str | None:
        return _canonical_unified_channel(str(spec.provider), spec.capability, spec.period)

    @classmethod
    def _reject_core_channel_mismatch(cls, spec: QuerySpec, canonical: str | None) -> None:
        _reject_core_channel_mismatch(
            str(spec.provider),
            spec.capability,
            spec.channel,
            canonical,
        )

    @classmethod
    def _select_channel(cls, spec: QuerySpec) -> ChannelSpec:
        pid = str(spec.provider)
        cap = spec.capability
        canonical = cls._canonical_unified_channel(spec)

        if spec.channel:
            # 先判 canonical 不匹配，再确认 channel 是否注册：这样才能对
            # ``tdx + vipdoc`` 这类“channel 属于别的 Provider”的情况给出
            # canonical channel 诊断，而不是笼统的“未知 channel”。
            cls._reject_core_channel_mismatch(spec, canonical)
            PROVIDERS.require(pid, cap, channel=spec.channel)
            selected = PROVIDERS.get(pid).channel(spec.channel)
        elif canonical is not None:
            PROVIDERS.require(pid, cap, channel=canonical)
            selected = PROVIDERS.get(pid).channel(canonical)
        else:
            candidates = PROVIDERS.get(pid).channels_for(cap)
            if not candidates:
                raise ValidationError(
                    f"provider {pid!r} 不支持 capability {cap!r}",
                    context={"provider": pid, "capability": cap},
                )
            if len(candidates) != 1:
                raise ValidationError(
                    f"provider {pid!r} capability {cap!r} 有多个 channel，必须显式指定",
                    context={
                        "provider": pid,
                        "capability": cap,
                        "channels": [item.id for item in candidates],
                    },
                )
            selected = candidates[0]

        if cap == "bars":
            PROVIDERS.require_period(pid, selected.id, spec.period)
        return selected

    @classmethod
    def _validate_migrated_call(cls, spec: QuerySpec, channel: ChannelSpec) -> None:
        """Bind migrated-call arguments when the caller uses the payload convention.

        A capability served by the table-driven ``_migrated_capability`` backend
        may reach its implementation through ``options["args"] / options["kwargs"]``.
        When that payload is present it is bound against the real implementation
        signature *here*, so a caller who opted into the raw payload convention
        gets a planning-time :class:`~atst.errors.ValidationError`.

        The convention is deliberately optional: the typed path
        (:meth:`atst.Client.typed` via
        :func:`atst.typed_query.call_payload_from_typed`) compiles the same
        capability names from semantic fields with no raw positional payload,
        and validates at dispatch instead. Requiring the payload at planning
        would reject that first-class path, so the hard guarantee is enforced
        where it is universal — before Provider I/O in
        :meth:`~atst.runtime.executor.DirectProviderExecutor._migrated_capability`.
        """

        from ..providers import binding_for
        from ..runtime.capability_contract import validate_call

        options = spec.options
        if "args" not in options and "kwargs" not in options:
            return
        key = (str(spec.provider), channel.id, spec.capability)
        try:
            binding_for(*key)
        except KeyError:
            return
        args = options.get("args", [])
        kwargs = options.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError(
                "migrated capability options 必须包含 args:list / kwargs:object",
                context={
                    "provider": key[0],
                    "channel": key[1],
                    "capability": key[2],
                    "phase": "query_validation",
                },
            )
        validate_call(key[0], key[1], key[2], tuple(args), dict(kwargs))

    def compile(self, spec: QuerySpec) -> QueryPlan:
        from ..runtime.autoselect import is_auto_provider

        #: P1-6：先记下「调用方请求的是 auto」，_resolve_auto 会把它替换成具体
        #: Provider，这个事实只剩这里能抓到。
        auto_requested = is_auto_provider(spec.provider) or (
            spec.provider is None and is_auto_provider(self.default_provider)
        )
        normalized = self._resolve_auto(spec).normalized(default_provider=self.default_provider)
        selected = self._select_channel(normalized)
        self._validate_migrated_call(normalized, selected)
        currentness = _parse_currentness(normalized.currentness)
        if currentness is CurrentnessMode.LIVE and not selected.live:
            raise ValidationError(
                "所选 Channel 不是 live channel，不能满足 currentness='live'",
                context={
                    "provider": normalized.provider,
                    "channel": selected.id,
                    "capability": normalized.capability,
                    "currentness": currentness.value,
                    "local": selected.local,
                },
            )
        fingerprint = QueryFingerprint.from_normalized_spec(
            normalized,
            channel=selected.id,
        )
        return QueryPlan(
            spec=normalized,
            provider=str(normalized.provider),
            channel=selected.id,
            fingerprint=fingerprint,
            budget=ExecutionBudget.from_deadline_ms(normalized.deadline_ms),
            requested_provider="auto" if auto_requested else None,
        )
