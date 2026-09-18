# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical query contracts and deterministic single-Provider planning.

This module is intentionally side-effect free: planning validates capability,
Provider, Channel, period and currentness *before* any service I/O. One
:class:`QueryPlan` always binds to exactly one Provider and one canonical
Channel. Cross-Provider fallback is an orchestration policy and must never occur
inside the planner.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .domain.period import normalize_bar_period
from .domain.symbol import normalize_symbol
from .error_envelope import is_sensitive_key
from .errors import ReadTimeout, ValidationError
from .providers import PROVIDERS, ChannelSpec, resolve_provider

__all__ = [
    "CurrentnessMode",
    "ExecutionBudget",
    "QuerySpec",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
]

_MINUTE_PERIODS = frozenset({"1min", "5min", "15min", "30min", "60min"})
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


class CurrentnessMode(str, Enum):
    """Caller intent for how current a result must be."""

    AUTO = "auto"
    LIVE = "live"
    HISTORICAL = "historical"
    BUSINESS = "business"


@dataclass(slots=True)
class ExecutionBudget:
    """One total monotonic deadline shared by the whole logical query."""

    deadline_ns: int
    max_attempts: int = 1
    attempts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_deadline_ms(cls, deadline_ms: int, *, max_attempts: int = 1) -> ExecutionBudget:
        if deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0",
                context={"deadline_ms": deadline_ms},
            )
        if max_attempts <= 0:
            raise ValidationError(
                "max_attempts 必须大于 0",
                context={"max_attempts": max_attempts},
            )
        return cls(
            deadline_ns=time.monotonic_ns() + int(deadline_ms * 1_000_000),
            max_attempts=max_attempts,
        )

    def remaining_ns(self) -> int:
        return max(0, self.deadline_ns - time.monotonic_ns())

    def remaining_s(self) -> float:
        return self.remaining_ns() / 1_000_000_000

    def ensure_remaining(self, phase: str) -> None:
        if self.remaining_ns() <= 0:
            raise ReadTimeout(
                "查询总 deadline 已耗尽",
                context={"phase": phase, "deadline_scope": "query"},
            )

    def begin_attempt(self, phase: str = "provider_request") -> None:
        self.ensure_remaining(phase)
        with self._lock:
            if self.attempts >= self.max_attempts:
                raise ReadTimeout(
                    "查询执行次数已耗尽",
                    context={
                        "phase": phase,
                        "attempts": self.attempts,
                        "max_attempts": self.max_attempts,
                        "deadline_scope": "query",
                    },
                )
            self.attempts += 1


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


def _canonical_unified_channel(provider: str, capability: str, period: str) -> str | None:
    """Return the single channel the unified ``quotes``/``bars`` executor uses.

    ``None`` means the (provider, capability) pair has no unified canonical
    channel, so an explicit provider-internal channel must be honored verbatim.
    """

    pid = str(provider)
    cap = str(capability)
    if pid == "tencent" and cap == "bars":
        return "minute_kline" if period in _MINUTE_PERIODS else "kline"
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


def _canonical_options(options: Mapping[str, Any] | None) -> str:
    try:
        return json.dumps(
            dict(options or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "query options 必须是可 JSON 序列化的确定性值",
            context={"options_type": type(options).__name__},
        ) from exc


def _parse_currentness(value: str | CurrentnessMode) -> CurrentnessMode:
    if isinstance(value, CurrentnessMode):
        return value
    normalized = str(value or CurrentnessMode.AUTO.value).strip().lower()
    try:
        return CurrentnessMode(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"未知 currentness {value!r}",
            context={"currentness": value, "allowed": [item.value for item in CurrentnessMode]},
        ) from exc


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """Immutable semantic request before Provider execution."""

    capability: str
    symbols: tuple[str, ...] = ()
    provider: str | None = None
    channel: str | None = None
    period: str = ""
    count: int = 0
    start: int = 0
    adjustment: str = ""
    currentness: str = CurrentnessMode.AUTO.value
    max_age: float | None = None
    deadline_ms: int = 5000
    schema_version: int = 1
    options_json: str = "{}"

    @classmethod
    def build(
        cls,
        capability: str,
        *,
        symbols: str | Sequence[str] = (),
        provider: str | None = None,
        channel: str | None = None,
        period: str = "",
        count: int = 0,
        start: int = 0,
        adjustment: str = "",
        allow_partial: bool = False,
        allow_stale: bool = False,
        currentness: str | CurrentnessMode = CurrentnessMode.AUTO,
        max_age: float | None = None,
        deadline_ms: int = 5000,
        schema_version: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> QuerySpec:
        symbol_tuple = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        current = _parse_currentness(currentness)
        # v13 SSOT：``allow_partial`` / ``allow_stale`` 不再是 QuerySpec 一等字段，
        # 但仍作为构造期 ergonomic 开关保留，统一折叠进 ``options`` 扩展袋。
        merged_options: dict[str, Any] = dict(options or {})
        if allow_partial:
            merged_options["allow_partial"] = True
        if allow_stale:
            merged_options["allow_stale"] = True
        return cls(
            capability=capability,
            symbols=symbol_tuple,
            provider=provider,
            channel=channel,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=current.value,
            max_age=max_age,
            deadline_ms=deadline_ms,
            schema_version=schema_version,
            options_json=_canonical_options(merged_options),
        )

    @property
    def options(self) -> dict[str, Any]:
        value = json.loads(self.options_json or "{}")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def allow_partial(self) -> bool:
        """Partial-batch tolerance, expressed through the ``options`` bag.

        v13 SSOT：``QuerySpec`` 不再把 ``source`` / ``route`` / ``allow_partial``
        作为一等字段（历史 route 选择器已由 ``provider`` 取代）。批量容忍度
        作为 capability 级别的可扩展选项保留在 ``options`` 中，语义不变。
        """

        return bool(self.options.get("allow_partial", False))

    def normalized(self, *, default_provider: str | None = None) -> QuerySpec:
        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")

        # A-share/quote/bar capabilities use the canonical security-symbol
        # engine.  Derivative identifiers are provider-native contracts (for
        # example ``IF2509`` and Eastmoney option quote ids) and must remain
        # opaque; forcing them through the A-share parser rejects valid
        # canonical capabilities before Provider execution.
        opaque_symbol_capabilities = frozenset(
            {
                "bond_kline",
                "futures_kline",
                "options_snapshot",
                "options_kline",
            }
        )
        if cap in opaque_symbol_capabilities:
            symbols = tuple(str(item).strip() for item in self.symbols)
            if any(not item for item in symbols):
                raise ValidationError(
                    f"{cap} 的 symbol 不能为空",
                    context={"capability": cap},
                )
        else:
            symbols = tuple(normalize_symbol(item) for item in self.symbols)
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(f"{cap} 至少需要一个 symbol", context={"capability": cap})
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError("bars 统一 QuerySpec 一次只接受一个 symbol")
        if self.options.get("allow_stale"):
            raise ValidationError(
                "allow_stale 策略尚未实现；如需容忍过期数据请显式提高 max_age",
                context={"capability": cap, "allow_stale": True},
            )
        if self.allow_partial and cap != "quotes":
            raise ValidationError(
                "allow_partial 仅支持 quotes 批量能力（quotes BatchResult）",
                context={"capability": cap, "allow_partial": True},
            )
        if self.count < 0:
            raise ValidationError("count 不能为负数", context={"count": self.count})
        if cap == "bars" and self.count <= 0:
            raise ValidationError("bars.count 必须大于 0", context={"count": self.count})
        if self.start < 0:
            raise ValidationError("start 不能为负数", context={"start": self.start})
        if self.deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0", context={"deadline_ms": self.deadline_ms}
            )
        if self.schema_version <= 0:
            raise ValidationError("schema_version 必须大于 0")
        if self.max_age is not None and self.max_age < 0:
            raise ValidationError("max_age 不能为负数", context={"max_age": self.max_age})

        currentness = _parse_currentness(self.currentness)
        selected = resolve_provider(
            provider=self.provider,
            default=default_provider or PROVIDERS.default_provider,
        )
        channel = _norm_text(self.channel) or None
        period = normalize_bar_period(self.period) if cap == "bars" else _norm_text(self.period)
        # v13 SSOT：统一 quotes/bars 只能走 Provider 的 canonical channel。该检查必须先于
        # 注册表存在性校验，否则 ``provider=tdx, channel=vipdoc`` 会先报“未知 channel”，
        # 掩盖真正的“canonical channel 不匹配”语义。
        _reject_core_channel_mismatch(
            selected,
            cap,
            channel,
            _canonical_unified_channel(selected, cap, period),
        )
        PROVIDERS.require(selected, cap, channel=channel)

        max_age = None if self.max_age in (None, 0, 0.0) else float(self.max_age)
        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=selected,
            channel=channel,
            period=period,
            adjustment=_norm_text(self.adjustment),
            currentness=currentness.value,
            max_age=max_age,
            options_json=_canonical_options(self.options),
        )


def _secret_digest(value: Any) -> str:
    """Stable non-reversible placeholder for a credential value.

    The digest keeps the fingerprint deterministic — the same credential still
    yields the same identity, so caching and single-flight de-duplication keep
    working — while the plaintext never reaches a fingerprint, log or cache key.
    """

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "__secret_sha256__" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _redact_secrets(value: Any) -> Any:
    """Recursively replace credential-bearing values with digests."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                _secret_digest(item)
                if is_sensitive_key(str(key))
                else _redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    """Stable full semantic identity used by cache/single-flight layers."""

    value: str
    canonical: str

    @staticmethod
    def _payload(spec: QuerySpec, *, channel: str) -> dict[str, Any]:
        # v13 SSOT：fingerprint 只描述**数据身份**（问的是什么），不描述**新鲜度策略**
        # （允许多旧）。``max_age`` 是调用方给出的缓存/新鲜度上界，同一份数据的
        # 不同 max_age 必须共享一个身份，缓存的过期判定在读取时按实际 age 计算。
        #
        # ``options`` 会携带调用方凭证（例如 wencai 的 ``cookie``）：它们参与
        # 身份判定（换凭证即换身份），但**绝不能**以明文进入 fingerprint / 缓存键 /
        # 日志，故按敏感键递归替换为 sha256 占位符。
        return {
            "schema_version": spec.schema_version,
            "capability": spec.capability,
            "provider": spec.provider,
            "channel": str(channel).strip().lower(),
            "symbols": list(spec.symbols),
            "period": spec.period,
            "count": spec.count,
            "start": spec.start,
            "adjustment": spec.adjustment,
            "currentness": spec.currentness,
            "options": _redact_secrets(spec.options),
        }

    @classmethod
    def from_normalized_spec(cls, spec: QuerySpec, *, channel: str) -> QueryFingerprint:
        canonical = json.dumps(
            cls._payload(spec, channel=channel),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(value=f"q{spec.schema_version}:{digest}", canonical=canonical)

    @classmethod
    def from_spec(
        cls,
        spec: QuerySpec,
        *,
        channel: str,
        default_provider: str | None = None,
    ) -> QueryFingerprint:
        return cls.from_normalized_spec(
            spec.normalized(default_provider=default_provider), channel=channel
        )


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Compiled executable identity. It can never contain fallback Providers."""

    spec: QuerySpec
    provider: str
    channel: str
    fingerprint: QueryFingerprint
    deadline_ms: int
    batch_limit: int | None
    live_channel: bool
    local_channel: bool
    #: 单次逻辑查询共享的总 deadline/尝试预算。属于**运行时状态**而非编译身份，
    #: 因此刻意排除在相等性与哈希之外（两个独立编译、语义相同的 plan 仍相等）。
    budget: ExecutionBudget = field(compare=False, repr=False)


class QueryPlanner:
    """Compile semantic requests into deterministic one-Provider plans."""

    def __init__(self, *, default_provider: str | None = None) -> None:
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
        PROVIDERS.get(selected)
        self.default_provider = resolve_provider(provider=selected)

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
        gets a planning-time :class:`~tstdx.errors.ValidationError`.

        The convention is deliberately optional: the typed path
        (:meth:`tstdx.Client.typed` via
        :func:`tstdx.typed_query.call_payload_from_typed`) compiles the same
        capability names from semantic fields with no raw positional payload,
        and validates at dispatch instead. Requiring the payload at planning
        would reject that first-class path, so the hard guarantee is enforced
        where it is universal — before Provider I/O in
        :meth:`~tstdx.direct_provider.DirectProviderExecutor._migrated_capability`.
        """

        from .capability_catalog import binding_for, validate_call

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
        normalized = spec.normalized(default_provider=self.default_provider)
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
            deadline_ms=normalized.deadline_ms,
            batch_limit=selected.batch_limit_for(normalized.capability),
            live_channel=selected.live,
            local_channel=selected.local,
            budget=ExecutionBudget.from_deadline_ms(normalized.deadline_ms, max_attempts=1),
        )
