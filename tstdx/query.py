# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical query contracts and deterministic planning for v12."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from .domain.period import normalize_bar_period
from .domain.symbol import normalize_symbol
from .errors import ValidationError
from .execution import ExecutionBudget
from .providers import PROVIDERS, resolve_provider

__all__ = ["QuerySpec", "QueryFingerprint", "QueryPlan", "QueryPlanner"]

_MINUTE_PERIODS = frozenset({"1min", "5min", "15min", "30min", "60min"})
_QUOTE_CHANNELS = {
    "tdx": "quotation",
    "tencent": "quote",
    "sina": "quote",
    "eastmoney": "quote",
    "baidu": "quote",
}


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


def _resolve_default_provider(value: str | None) -> str:
    chosen = PROVIDERS.default_provider if value is None else value
    if not str(chosen).strip():
        raise ValidationError("default_provider 不能为空")
    return resolve_provider(provider=chosen)


@dataclass(frozen=True, slots=True)
class QuerySpec:
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
    max_age: float | None = None
    allow_stale: bool = False
    deadline_ms: int = 5000
    schema_version: int = 1

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
        max_age: float | None = None,
        allow_stale: bool = False,
        deadline_ms: int = 5000,
        schema_version: int = 1,
    ) -> "QuerySpec":
        symbol_tuple = (symbols,) if isinstance(symbols, str) else tuple(symbols)
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
            max_age=max_age,
            allow_stale=allow_stale,
            deadline_ms=deadline_ms,
            schema_version=schema_version,
        )

    def normalized(self, *, default_provider: str | None = None) -> "QuerySpec":
        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")
        symbols = tuple(normalize_symbol(item) for item in self.symbols)
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(f"{cap} 至少需要一个 symbol", context={"capability": cap})
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError("bars 当前统一契约一次只接受一个 symbol")
        if self.allow_partial and cap != "quotes":
            raise ValidationError(
                "allow_partial 当前仅支持 quotes BatchResult",
                context={"allow_partial": True, "capability": cap},
            )
        if self.count < 0:
            raise ValidationError("count 不能为负数", context={"count": self.count})
        if cap == "bars" and self.count == 0:
            raise ValidationError("bars.count 必须大于 0", context={"count": self.count})
        if self.start < 0:
            raise ValidationError("start 不能为负数", context={"start": self.start})
        if self.deadline_ms <= 0:
            raise ValidationError("deadline_ms 必须大于 0", context={"deadline_ms": self.deadline_ms})
        if self.schema_version <= 0:
            raise ValidationError("schema_version 必须大于 0")
        if self.max_age is not None and self.max_age < 0:
            raise ValidationError("max_age 不能为负数", context={"max_age": self.max_age})
        if self.allow_stale:
            raise ValidationError(
                "allow_stale 尚未启用：v12 当前不会在 Provider 失败后返回过期缓存",
                context={"allow_stale": True, "fallback": False},
            )

        resolved_default = _resolve_default_provider(default_provider)
        pid = resolve_provider(
            provider=self.provider,
            source=self.source,
            default=resolved_default,
        )
        PROVIDERS.require(pid, cap, channel=self.channel)

        period = normalize_bar_period(self.period) if cap == "bars" else _norm_text(self.period)
        max_age = None if self.max_age in (None, 0, 0.0) else float(self.max_age)
        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=pid,
            source=None,
            channel=_norm_text(self.channel) or None,
            period=period,
            adjustment=_norm_text(self.adjustment),
            max_age=max_age,
            allow_partial=bool(self.allow_partial),
            allow_stale=False,
        )


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
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
            "max_age": spec.max_age,
            "allow_stale": spec.allow_stale,
        }

    @classmethod
    def from_normalized_spec(cls, spec: QuerySpec, *, channel: str) -> "QueryFingerprint":
        canonical = json.dumps(
            cls._payload(spec, channel=channel),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
    spec: QuerySpec
    provider: str
    channel: str
    fingerprint: QueryFingerprint
    budget: ExecutionBudget


class QueryPlanner:
    """Compile semantic queries into deterministic single-Provider plans."""

    def __init__(self, *, default_provider: str | None = None) -> None:
        self.default_provider = _resolve_default_provider(default_provider)

    @staticmethod
    def _require_bar_period(provider: str, channel: str, period: str) -> None:
        PROVIDERS.require_period(provider, channel, period)

    @classmethod
    def _canonical_unified_channel(cls, spec: QuerySpec) -> str | None:
        """Return the Channel the current unified executor can actually execute.

        Provider-specific/local Channels stay valid Registry facts and Direct API
        targets, but they must never be accepted into a QueryPlan whose executor
        would silently call a different Channel.
        """
        pid = str(spec.provider)
        cap = spec.capability
        if cap == "quotes":
            return _QUOTE_CHANNELS.get(pid)
        if cap == "bars":
            if pid == "tdx":
                return "quotation"
            if pid == "tencent":
                return "minute_kline" if spec.period in _MINUTE_PERIODS else "kline"
            if pid == "sina":
                return "history_kline"
            if pid in {"eastmoney", "baidu"}:
                return "kline"
        return None

    @classmethod
    def _reject_channel_mismatch(cls, spec: QuerySpec, canonical: str | None) -> None:
        if spec.channel is None or canonical is None or spec.channel == canonical:
            return
        raise ValidationError(
            f"Unified {spec.capability} QueryPlan 不能执行 provider {spec.provider!r} "
            f"channel {spec.channel!r}；当前 canonical channel 为 {canonical!r}。"
            "Provider-specific/local Channel 请使用 Direct Provider/Reader API。",
            context={
                "provider": spec.provider,
                "channel": spec.channel,
                "canonical_channel": canonical,
                "capability": spec.capability,
                "channel_switch_allowed": False,
                "provider_switch_allowed": False,
            },
        )

    @classmethod
    def _default_channel(cls, spec: QuerySpec) -> str:
        pid = str(spec.provider)
        cap = spec.capability
        canonical = cls._canonical_unified_channel(spec)
        if spec.channel:
            PROVIDERS.require(pid, cap, channel=spec.channel)
            if cap == "bars":
                cls._require_bar_period(pid, spec.channel, spec.period)
            cls._reject_channel_mismatch(spec, canonical)
            return spec.channel
        if canonical is not None:
            PROVIDERS.require(pid, cap, channel=canonical)
            if cap == "bars":
                cls._require_bar_period(pid, canonical, spec.period)
            return canonical
        candidates = PROVIDERS.get(pid).channels_for(cap)
        if len(candidates) == 1:
            if cap == "bars":
                cls._require_bar_period(pid, candidates[0].id, spec.period)
            return candidates[0].id
        if not candidates:
            raise ValidationError(
                f"provider {pid!r} 不支持 capability {cap!r}",
                context={"provider": pid, "capability": cap},
            )
        raise ValidationError(
            f"provider {pid!r} 的 capability {cap!r} 存在多个 channel，必须显式指定",
            context={"provider": pid, "capability": cap, "channels": [c.id for c in candidates]},
        )

    def compile(self, spec: QuerySpec) -> QueryPlan:
        normalized = spec.normalized(default_provider=self.default_provider)
        channel = self._default_channel(normalized)
        return QueryPlan(
            spec=normalized,
            provider=str(normalized.provider),
            channel=channel,
            fingerprint=QueryFingerprint.from_normalized_spec(normalized, channel=channel),
            budget=ExecutionBudget.from_deadline_ms(normalized.deadline_ms),
        )
