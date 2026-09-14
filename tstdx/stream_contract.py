# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 streaming request and planning contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain.symbol import normalize_symbol
from .errors import ValidationError
from .providers import PROVIDERS, resolve_provider

__all__ = ["StreamSpec", "StreamPlan", "StreamPlanner"]


@dataclass(frozen=True, slots=True)
class StreamSpec:
    capability: str
    symbols: tuple[str, ...]
    provider: str = "tdx"
    channel: str | None = None
    interval: float = 1.0
    diff_only: bool = False
    max_queue: int = 1024

    @classmethod
    def build(
        cls,
        symbols: str | Sequence[str],
        *,
        provider: str = "tdx",
        channel: str | None = None,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
    ) -> StreamSpec:
        values = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        return cls(
            capability="quotes",
            symbols=values,
            provider=provider,
            channel=channel,
            interval=float(interval),
            diff_only=bool(diff_only),
            max_queue=int(max_queue),
        )


@dataclass(frozen=True, slots=True)
class StreamPlan:
    capability: str
    symbols: tuple[str, ...]
    provider: str
    channel: str
    interval: float
    diff_only: bool
    max_queue: int


class StreamPlanner:
    """Compile one exact Provider streaming plan before worker startup."""

    def compile(self, spec: StreamSpec) -> StreamPlan:
        capability = str(spec.capability).strip().lower()
        if capability != "quotes":
            raise ValidationError(
                "v13 Stateful streaming 当前仅支持 quotes capability",
                context={"capability": capability},
            )
        symbols = tuple(normalize_symbol(item) for item in spec.symbols)
        if not symbols:
            raise ValidationError("stream symbols 不能为空")
        if spec.interval <= 0:
            raise ValidationError("stream interval 必须大于 0")
        if spec.max_queue < 0:
            raise ValidationError("stream max_queue 不能为负数")

        provider = resolve_provider(provider=spec.provider)
        # Current StatefulQuoteStream is backed by the TDX quotation transport.
        # Fail closed instead of pretending other Providers share that stream.
        if provider != "tdx":
            raise ValidationError(
                "Stateful quotes stream 当前只存在 tdx Direct stream binding",
                context={"provider": provider, "capability": capability},
            )
        channel = str(spec.channel or "quotation").strip().lower()
        if channel != "quotation":
            raise ValidationError(
                "tdx quotes stream canonical channel 为 quotation",
                context={"provider": provider, "channel": channel},
            )
        PROVIDERS.require(provider, "quotes", channel=channel)
        return StreamPlan(
            capability=capability,
            symbols=symbols,
            provider=provider,
            channel=channel,
            interval=float(spec.interval),
            diff_only=bool(spec.diff_only),
            max_queue=int(spec.max_queue),
        )
