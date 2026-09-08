# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical query contracts and deterministic planning for v12.

The public execution model is::

    QuerySpec -> QueryPlanner -> QueryPlan -> Provider execution

A plan binds exactly one Provider and one Provider-internal Channel.  Provider
selection is therefore completed before I/O starts; runtime failures never
cause the planner to compile or execute another Provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from .domain.symbol import normalize_symbol
from .errors import ValidationError
from .execution import ExecutionBudget
from .providers import PROVIDERS, resolve_provider

__all__ = [
    "QuerySpec",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
]

_MINUTE_PERIODS = frozenset(
    {
        "1m",
        "1min",
        "min",
        "5m",
        "5min",
        "15m",
        "15min",
        "30m",
        "30min",
        "60m",
        "60min",
    }
)


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """User-visible semantic query contract.

    ``provider`` is the formal selector. ``source`` remains a compatibility
    alias and is resolved into the same Provider id during planning.

    ``max_age`` and ``allow_stale`` are part of the semantic fingerprint even
    though the first v12 execution stage keeps live cache disabled by default.
    This prevents future cache integration from accidentally sharing results
    between freshness policies.
    """

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
        symbols: str | tuple[str, ...] | list[str] = (),
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
        if isinstance(symbols, str):
            symbol_tuple = (symbols,)
        else:
            symbol_tuple = tuple(symbols)
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

    def normalized(self) -> "QuerySpec":
        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")

        symbols = tuple(normalize_symbol(item) for item in self.symbols)
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(
                f"{cap} 至少需要一个 symbol",
                context={"capability": cap},
            )
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError(
                "bars 当前统一契约一次只接受一个 symbol；批量请使用 query_many/BatchPlanner",
                context={"symbols": list(symbols)},
            )
        if self.count < 0:
            raise ValidationError("count 不能为负数", context={"count": self.count})
        if cap == "bars" and self.count == 0:
            raise ValidationError("bars.count 必须大于 0", context={"count": self.count})
        if self.start < 0:
            raise ValidationError("start 不能为负数", context={"start": self.start})
        if self.deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0",
                context={"deadline_ms": self.deadline_ms},
            )
        if self.schema_version <= 0:
            raise ValidationError(
                "schema_version 必须大于 0",
                context={"schema_version": self.schema_version},
            )
        if self.max_age is not None and self.max_age < 0:
            raise ValidationError(
                "max_age 不能为负数",
                context={"max_age": self.max_age},
            )

        pid = resolve_provider(
            provider=self.provider,
            source=self.source,
            default=PROVIDERS.default_provider,
        )
        PROVIDERS.require(pid, cap, channel=self.channel)

        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=pid,
            source=None,
            channel=_norm_text(self.channel) or None,
            period=_norm_text(self.period),
            adjustment=_norm_text(self.adjustment),
        )


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    """Stable semantic identity for cache/singleflight/trace correlation."""

    value: str
    canonical: str

    @classmethod
    def from_spec(cls, spec: QuerySpec, *, channel: str) -> "QueryFingerprint":
        normalized = spec.normalized()
        payload: dict[str, Any] = {
            "schema_version": normalized.schema_version,
            "capability": normalized.capability,
            "provider": normalized.provider,
            "channel": str(channel).strip().lower(),
            "symbols": list(normalized.symbols),
            "period": normalized.period,
            "count": normalized.count,
            "start": normalized.start,
            "adjustment": normalized.adjustment,
            "allow_partial": normalized.allow_partial,
            "max_age": normalized.max_age,
            "allow_stale": normalized.allow_stale,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(value=f"q{normalized.schema_version}:{digest}", canonical=canonical)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Compiled one-Provider execution plan."""

    spec: QuerySpec
    provider: str
    channel: str
    fingerprint: QueryFingerprint
    budget: ExecutionBudget


class QueryPlanner:
    """Compile semantic queries into deterministic Provider-bound plans."""

    @staticmethod
    def _default_channel(spec: QuerySpec) -> str:
        pid = str(spec.provider)
        cap = spec.capability
        if spec.channel:
            PROVIDERS.require(pid, cap, channel=spec.channel)
            return spec.channel

        if cap == "quotes":
            preferred = {
                "tdx": "quotation",
                "tencent": "quote",
                "sina": "quote",
                "eastmoney": "quote",
                "baidu": "quote",
            }.get(pid)
            if preferred is not None:
                PROVIDERS.require(pid, cap, channel=preferred)
                return preferred

        if cap == "bars":
            if pid == "tdx":
                return "quotation"
            if pid == "tencent":
                return "minute_kline" if spec.period in _MINUTE_PERIODS else "kline"
            if pid == "sina":
                return "history_kline"
            if pid in {"eastmoney", "baidu"}:
                return "kline"

        candidates = PROVIDERS.get(pid).channels_for(cap)
        if len(candidates) == 1:
            return candidates[0].id
        if not candidates:
            raise ValidationError(
                f"provider {pid!r} 不支持 capability {cap!r}",
                context={"provider": pid, "capability": cap},
            )
        raise ValidationError(
            f"provider {pid!r} 的 capability {cap!r} 存在多个 channel，必须显式指定",
            context={
                "provider": pid,
                "capability": cap,
                "channels": [item.id for item in candidates],
            },
        )

    def compile(self, spec: QuerySpec) -> QueryPlan:
        normalized = spec.normalized()
        channel = self._default_channel(normalized)
        fingerprint = QueryFingerprint.from_spec(normalized, channel=channel)
        budget = ExecutionBudget.from_deadline_ms(normalized.deadline_ms)
        return QueryPlan(
            spec=normalized,
            provider=str(normalized.provider),
            channel=channel,
            fingerprint=fingerprint,
            budget=budget,
        )
