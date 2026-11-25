# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical v13 streaming request and planning contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain.symbol import normalize_symbol
from .errors import ValidationError
from .providers import PROVIDERS, resolve_provider
from .streaming.base import validate_subscription

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
    """Exactly the values ``Client.stream`` hands to a worker.

    ``capability`` and ``channel`` are deliberately absent: ``compile`` fail-closes
    on both, and keeping a second copy in the plan would be a claim no code reads.
    """

    symbols: tuple[str, ...]
    provider: str
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
        # P2-6/L2：订阅形状校验（symbols 空 / interval / max_queue）收口到
        # :func:`atst.streaming.base.validate_subscription` 这一份实现——基类
        # ``subscribe`` 与门面 ``subscribe`` 的防御断言也调它，三处不再各抄一份，
        # 错误类型统一为 :class:`SubscriptionError`（过去 compile 这份抛
        # ``ValidationError``，同一坏入参经 ``Client.stream`` 与经 ``subscribe``
        # 报不同类型）。
        symbols_values = validate_subscription(
            list(spec.symbols), interval=spec.interval, max_queue=spec.max_queue
        )
        symbols = tuple(normalize_symbol(item) for item in symbols_values)

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
            symbols=symbols,
            provider=provider,
            interval=float(spec.interval),
            diff_only=bool(spec.diff_only),
            max_queue=int(spec.max_queue),
        )
