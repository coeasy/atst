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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from .domain.period import normalize_bar_period
from .domain.symbol import normalize_symbol
from .errors import ValidationError
from .providers import PROVIDERS, ChannelSpec, resolve_provider

__all__ = [
    "CurrentnessMode",
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


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


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
    source: str | None = None
    channel: str | None = None
    period: str = ""
    count: int = 0
    start: int = 0
    adjustment: str = ""
    allow_partial: bool = False
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
        source: str | None = None,
        channel: str | None = None,
        period: str = "",
        count: int = 0,
        start: int = 0,
        adjustment: str = "",
        allow_partial: bool = False,
        currentness: str | CurrentnessMode = CurrentnessMode.AUTO,
        max_age: float | None = None,
        deadline_ms: int = 5000,
        schema_version: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> "QuerySpec":
        symbol_tuple = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        current = _parse_currentness(currentness)
        return cls(
            capability=capability,
            symbols=symbol_tuple,
            provider=provider,
            source=source,
            channel=channel,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            allow_partial=allow_partial,
            currentness=current.value,
            max_age=max_age,
            deadline_ms=deadline_ms,
            schema_version=schema_version,
            options_json=_canonical_options(options),
        )

    @property
    def options(self) -> dict[str, Any]:
        value = json.loads(self.options_json or "{}")
        return dict(value) if isinstance(value, dict) else {}

    def normalized(self, *, default_provider: str | None = None) -> "QuerySpec":
        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")

        symbols = tuple(normalize_symbol(item) for item in self.symbols)
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(f"{cap} 至少需要一个 symbol", context={"capability": cap})
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError("bars 统一 QuerySpec 一次只接受一个 symbol")
        if self.allow_partial and cap != "quotes":
            raise ValidationError(
                "allow_partial 当前仅允许 quotes 批量能力",
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
            source=self.source,
            default=default_provider or PROVIDERS.default_provider,
        )
        channel = _norm_text(self.channel) or None
        PROVIDERS.require(selected, cap, channel=channel)

        period = normalize_bar_period(self.period) if cap == "bars" else _norm_text(self.period)
        max_age = None if self.max_age in (None, 0, 0.0) else float(self.max_age)
        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=selected,
            source=None,
            channel=channel,
            period=period,
            adjustment=_norm_text(self.adjustment),
            allow_partial=bool(self.allow_partial),
            currentness=currentness.value,
            max_age=max_age,
            options_json=_canonical_options(self.options),
        )


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    """Stable full semantic identity used by cache/single-flight layers."""

    value: str
    canonical: str

    @staticmethod
    def _payload(spec: QuerySpec, *, channel: str) -> dict[str, Any]:
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
            "allow_partial": spec.allow_partial,
            "currentness": spec.currentness,
            "max_age": spec.max_age,
            "options": spec.options,
        }

    @classmethod
    def from_normalized_spec(cls, spec: QuerySpec, *, channel: str) -> "QueryFingerprint":
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
    ) -> "QueryFingerprint":
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


class QueryPlanner:
    """Compile semantic requests into deterministic one-Provider plans."""

    def __init__(self, *, default_provider: str | None = None) -> None:
        selected = default_provider or PROVIDERS.default_provider
        PROVIDERS.get(selected)
        self.default_provider = resolve_provider(provider=selected)

    @staticmethod
    def _canonical_unified_channel(spec: QuerySpec) -> str | None:
        pid = str(spec.provider)
        cap = spec.capability
        if pid == "tencent" and cap == "bars":
            return "minute_kline" if spec.period in _MINUTE_PERIODS else "kline"
        return _CANONICAL_UNIFIED_CHANNELS.get((pid, cap))

    @classmethod
    def _reject_core_channel_mismatch(cls, spec: QuerySpec, canonical: str | None) -> None:
        if spec.channel is None or canonical is None or spec.channel == canonical:
            return
        if spec.capability not in {"quotes", "bars"}:
            return
        raise ValidationError(
            f"统一 {spec.capability} QueryPlan 不能执行 provider {spec.provider!r} "
            f"channel {spec.channel!r}；canonical channel 为 {canonical!r}",
            context={
                "provider": spec.provider,
                "channel": spec.channel,
                "canonical_channel": canonical,
                "capability": spec.capability,
                "provider_switch_allowed": False,
                "channel_switch_allowed": False,
            },
        )

    @classmethod
    def _select_channel(cls, spec: QuerySpec) -> ChannelSpec:
        pid = str(spec.provider)
        cap = spec.capability
        canonical = cls._canonical_unified_channel(spec)

        if spec.channel:
            PROVIDERS.require(pid, cap, channel=spec.channel)
            cls._reject_core_channel_mismatch(spec, canonical)
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

    def compile(self, spec: QuerySpec) -> QueryPlan:
        normalized = spec.normalized(default_provider=self.default_provider)
        selected = self._select_channel(normalized)
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
        )
