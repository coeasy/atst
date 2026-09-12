# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Exact Provider/Channel execution bindings for the canonical runtime.

This module is deliberately fail-closed. A :class:`QueryPlan` resolves to one
Provider and one canonical Channel; execution never falls through to another
Provider. TDX host failover remains internal to ``TdxClient`` and is therefore
allowed inside the selected Provider boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import InternalError, TdxError, ValidationError
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


DIRECT_BINDINGS: tuple[DirectBinding, ...] = (
    DirectBinding("tdx", "quotation", "quotes", "_tdx_quotes"),
    DirectBinding("tdx", "quotation", "bars", "_tdx_bars"),
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


def audit_direct_bindings() -> tuple[DirectBinding, ...]:
    seen: set[tuple[str, str, str]] = set()
    for binding in DIRECT_BINDINGS:
        if binding.key in seen:
            raise RuntimeError(f"duplicate Direct binding: {binding.key!r}")
        seen.add(binding.key)
        PROVIDERS.require(binding.provider, binding.capability, channel=binding.channel)

    required: set[tuple[str, str, str]] = set()
    for provider in PROVIDERS.ids():
        spec = PROVIDERS.get(provider)
        for channel in spec.channels:
            for capability in channel.capabilities & {"quotes", "bars"}:
                if provider == "tdx" and channel.id != "quotation":
                    continue
                required.add((provider, channel.id, capability))

    missing = sorted(required - seen)
    if missing:
        raise RuntimeError(
            f"registered unified Provider capability has no Direct binding: {missing!r}"
        )
    return DIRECT_BINDINGS


class DirectProviderExecutor:
    """Execute one QueryPlan without any cross-Provider fallback."""

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
        audit_direct_bindings()
        self._bindings = {item.key: item for item in DIRECT_BINDINGS}

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        key = (plan.provider, plan.channel, plan.spec.capability)
        try:
            binding = self._bindings[key]
        except KeyError as exc:
            raise ValidationError(
                "QueryPlan 没有可执行 Direct Provider binding",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "fallback": False,
                    "provider_switch_allowed": False,
                },
            ) from exc
        fn = getattr(self, binding.executor_name)
        try:
            data = fn(plan)
        except TdxError as exc:
            exc.context.setdefault("provider", plan.provider)
            exc.context.setdefault("channel", plan.channel)
            exc.context.setdefault("capability", plan.spec.capability)
            exc.context.setdefault("fallback", False)
            exc.context.setdefault("provider_switch_allowed", False)
            raise
        except Exception as exc:
            raise InternalError(
                "Direct Provider executor 未处理异常",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "fallback": False,
                    "provider_switch_allowed": False,
                    "cause_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc
        return QueryResult.from_plan(data, plan=plan, provenance=Provenance.direct(plan))

    def _tdx_quotes(self, plan: QueryPlan) -> Any:
        from .client import TdxClient

        with TdxClient(hosts=self.hosts, timeout=self.timeout) as client:
            return client.quotes(list(plan.spec.symbols))

    def _tdx_bars(self, plan: QueryPlan) -> Any:
        from .client import TdxClient

        with TdxClient(hosts=self.hosts, timeout=self.timeout) as client:
            return client.bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                start=plan.spec.start,
            )

    def _local_bars(self, plan: QueryPlan) -> Any:
        if not self.vipdoc_root:
            raise ValidationError(
                "local_vipdoc Provider 需要显式 vipdoc_root",
                context={"provider": "local_vipdoc", "channel": "vipdoc"},
            )
        from .facade.api import UnifiedQuoteAPI

        api = UnifiedQuoteAPI(route="local", vipdoc_root=self.vipdoc_root, timeout=self.timeout)
        try:
            return api.bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                start=plan.spec.start,
                route="local",
            )
        finally:
            api.close()

    def _web_quotes(self, plan: QueryPlan) -> Any:
        from .web import get_quotes

        return get_quotes(list(plan.spec.symbols), source=plan.provider, timeout=self.timeout)

    def _tencent_bars(self, plan: QueryPlan) -> Any:
        from .web import create_source

        source_name = "minute_kline" if plan.channel == "minute_kline" else "kline"
        src = create_source(source_name, timeout=self.timeout)
        try:
            if plan.channel == "minute_kline":
                if plan.spec.adjustment:
                    raise ValidationError(
                        "Tencent minute_kline 不支持复权参数",
                        context={
                            "provider": "tencent",
                            "channel": "minute_kline",
                            "adjustment": plan.spec.adjustment,
                        },
                    )
                return src.fetch_bars(
                    plan.spec.symbols[0],
                    period=plan.spec.period,
                    count=plan.spec.count,
                )
            return src.fetch_bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _sina_bars(self, plan: QueryPlan) -> Any:
        from .web.history import SinaHistoryKlineSource

        src = SinaHistoryKlineSource(timeout=self.timeout)
        try:
            return src.fetch_bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _eastmoney_bars(self, plan: QueryPlan) -> Any:
        from .web.history import EastmoneyHistoryKlineSource

        src = EastmoneyHistoryKlineSource(timeout=self.timeout)
        try:
            return src.fetch_bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _baidu_bars(self, plan: QueryPlan) -> Any:
        from .web.adapters_baidu import BaiduSource

        if plan.spec.adjustment:
            raise ValidationError(
                "Baidu Direct bars 不支持复权参数",
                context={"provider": "baidu", "adjustment": plan.spec.adjustment},
            )
        src = BaiduSource(timeout=self.timeout)
        try:
            return src.fetch_kline(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
            )
        finally:
            src.close()
