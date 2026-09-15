# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Exact Provider/Channel execution bindings for the canonical v13 runtime."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capability_catalog import binding_for, validate_call
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


#: Tier-A / first-party capabilities owned by a dedicated executor method.
#: Every other ``(provider, channel, capability)`` triple in the canonical
#: registry dispatches through the table-driven ``_migrated_capability`` backend.
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
    """Resolve the executor method for one canonical registry triple."""
    return _CORE_EXECUTORS.get(key, "_migrated_capability")


def _registry_triples() -> tuple[tuple[str, str, str], ...]:
    """Every ``(provider, channel, capability)`` triple declared by the registry.

    The registry is the single source of truth for the executable surface, so
    ``DIRECT_BINDINGS`` is *generated* from it rather than hand-synced. This makes
    the registry ↔ binding bijection structural: a declared-but-unbound or
    bound-but-undeclared triple can no longer drift in silently.
    """
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


def audit_direct_bindings() -> tuple[DirectBinding, ...]:
    """Validate that DIRECT_BINDINGS are internally consistent.

    Duplicate bindings are a genuine developer error and remain a hard failure.
    Because :data:`DIRECT_BINDINGS` is generated from the registry, the
    registry ↔ binding bijection now holds by construction. What can still be
    *incomplete* is the migrated-capability catalog: a registry triple whose
    executor is :meth:`_migrated_capability` needs a
    :data:`~tstdx.capability_catalog.MIGRATED_BINDINGS` entry to route it. Such
    triples are declared-but-unroutable and reported once as a warning instead of
    raised, so a registry/executor mismatch cannot hard-block every
    ``UnifiedRuntime()`` / ``Client()`` construction.
    """
    seen: set[tuple[str, str, str]] = set()
    unroutable: list[tuple[str, str, str]] = []
    for binding in DIRECT_BINDINGS:
        if binding.key in seen:
            raise RuntimeError(f"duplicate Direct binding: {binding.key!r}")
        seen.add(binding.key)
        if binding.executor_name != "_migrated_capability":
            continue
        try:
            binding_for(*binding.key)
        except KeyError:
            unroutable.append(binding.key)
    if unroutable:
        warnings.warn(
            f"{len(unroutable)} 个 Direct binding 缺少 migrated-capability 执行元数据"
            f"（例如 {unroutable[0]!r}）；运行时按 binding 表派发，"
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

        return QueryResult.from_plan(
            data,
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def _tdx_client(self) -> Any:
        from .client import TdxClient

        return TdxClient(hosts=self.hosts, timeout=self.timeout)

    @staticmethod
    def _call_payload(plan: QueryPlan) -> tuple[list[Any], dict[str, Any]]:
        options = plan.spec.options
        args = options.get("args", [])
        kwargs = options.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError(
                "migrated capability options 必须包含 args:list / kwargs:object"
            )
        return args, dict(kwargs)

    def _migrated_capability(self, plan: QueryPlan) -> Any:
        meta = binding_for(plan.provider, plan.channel, plan.spec.capability)
        args, kwargs = self._call_payload(plan)
        validate_call(
            plan.provider,
            plan.channel,
            plan.spec.capability,
            tuple(args),
            kwargs,
        )

        if meta.backend == "web_session":
            from .web.facade import WebQuoteSession

            session = WebQuoteSession(meta.source or "sina", timeout=self.timeout)
            try:
                if meta.capability == "hk_quotes" and plan.provider in {
                    "sina",
                    "tencent",
                }:
                    kwargs.setdefault("provider", plan.provider)
                return getattr(session, meta.method)(*args, **kwargs)
            finally:
                session.close()

        if meta.backend == "tdx_client":
            with self._tdx_client() as client:
                if meta.capability == "quotes_concurrent":
                    kwargs.setdefault("as_format", "obj")
                return getattr(client, meta.method)(*args, **kwargs)

        if meta.backend == "f10_client":
            from .client import F10Client

            client = F10Client(timeout=self.timeout)
            try:
                if meta.capability == "f10":
                    return client.parse_text(client.download(args[0], args[1]))
                return list(client.catalog(args[0]))
            finally:
                client.close()

        if meta.backend in {"ex_client", "goods_client"}:
            from .client import ExMarketClient, GoodsClient

            client = (
                ExMarketClient(timeout=self.timeout)
                if meta.backend == "ex_client"
                else GoodsClient(timeout=self.timeout)
            )
            try:
                if meta.capability in {
                    "ex_bars",
                    "ex_quotes",
                    "goods_bars",
                    "goods_quotes",
                }:
                    kwargs.setdefault("as_format", "dict")
                return getattr(client, meta.method)(*args, **kwargs)
            finally:
                client.close()

        if meta.backend == "web_adapter":
            return self._web_adapter_call(
                meta.capability,
                plan.provider,
                args,
                kwargs,
            )
        if meta.backend == "composed":
            return self._composed_call(meta.capability, args, kwargs)

        raise ValidationError(
            "unknown migrated backend",
            context={"backend": meta.backend},
        )

    def _web_adapter_call(
        self,
        capability: str,
        provider: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        symbol = args[0]
        if capability == "minute_web":
            from .web.adapters_ext import MinuteSource

            src = MinuteSource(timeout=self.timeout)
            try:
                return src.fetch_minute(symbol)
            finally:
                src.close()

        if capability == "minute_klines":
            if provider == "eastmoney":
                from .web.history import EastmoneyHistoryKlineSource

                src = EastmoneyHistoryKlineSource(timeout=self.timeout)  # type: ignore[assignment]
            else:
                from .web.adapters_ext import MinuteKlineSource

                src = MinuteKlineSource(timeout=self.timeout)  # type: ignore[assignment]
            try:
                return src.fetch_bars(symbol, **kwargs)  # type: ignore[attr-defined]
            finally:
                src.close()

        if capability == "history":
            if provider == "eastmoney":
                from .web.history import (
                    EastmoneyHistoryKlineSource as Source,  # type: ignore[assignment]
                )
            else:
                from .web.history import (
                    SinaHistoryKlineSource as Source,  # type: ignore[assignment]
                )

            history = Source(timeout=self.timeout)  # type: ignore[call-arg]
            try:
                return history.fetch_bars(symbol, **kwargs)
            finally:
                history.close()

        raise ValidationError(
            "unknown web adapter capability",
            context={"capability": capability},
        )

    def _composed_call(
        self,
        capability: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Any:
        if capability == "security_list_all":
            market = args[0] if args else kwargs.pop("market", 0)
            rows: list[Any] = []
            start = 0
            with self._tdx_client() as client:
                while True:
                    page = list(client.security_list(market, start))
                    if not page:
                        break
                    rows.extend(page)
                    start += len(page)
            return rows

        if capability == "adjusted_bars":
            from .domain.adjust import AdjustEngine
            from .domain.finance import to_capital_changes
            from .domain.models import CapitalChange
            from .domain.symbol import split_symbol
            from .reader import DayBarReader

            symbol = str(args[0])
            method = str(kwargs.pop("method", "qfq"))
            period = str(kwargs.pop("period", "day"))
            count = int(kwargs.pop("count", 320))
            start = int(kwargs.pop("start", 0))
            events = kwargs.pop("events", None)
            anchor_date = kwargs.pop("anchor_date", None)
            if period != "day":
                raise ValidationError(
                    "adjusted_bars canonical local raw path currently supports day only",
                    context={"period": period},
                )
            if not self.vipdoc_root:
                raise ValidationError(
                    "adjusted_bars requires vipdoc_root for canonical raw bars"
                )

            market, code = split_symbol(symbol)
            path = Path(self.vipdoc_root) / market / "lday" / f"{market}{code}.day"
            bars = DayBarReader().read(path, output="model")
            end = max(0, len(bars) - start)
            begin = max(0, end - count)
            bars = bars[begin:end]

            if not events:
                with self._tdx_client() as client:
                    events = list(client.capital_changes(symbol))
            event_list = list(events)
            if event_list and not isinstance(event_list[0], CapitalChange):
                event_list = to_capital_changes(event_list)
            return AdjustEngine().apply(
                list(bars),
                event_list,
                method,
                anchor_date=anchor_date,
            )

        if capability == "sync_daily":
            from .sink import LocalDaySink

            symbols = args[0]
            root = kwargs.pop("root", None) or self.vipdoc_root
            profile = kwargs.pop("profile", "a_share_day")
            chunk = int(kwargs.pop("chunk", 800))
            max_windows = int(kwargs.pop("max_windows", 64))
            if not root:
                raise ValidationError("sync_daily requires root or vipdoc_root")
            sink = LocalDaySink(root, profile=profile)
            out: dict[str, Any] = {}
            with self._tdx_client() as client:
                for symbol in symbols:
                    normalized = str(symbol)

                    def fetch(
                        offset: int,
                        count: int,
                        _symbol: str = normalized,
                    ) -> list[Any]:
                        return client.bars(
                            _symbol,
                            period="day",
                            count=count,
                            start=offset,
                            as_format="dict",
                        )

                    result = sink.sync(
                        normalized,
                        fetch,
                        chunk=chunk,
                        max_windows=max_windows,
                    )
                    out[normalized] = {
                        "added": result.added,
                        "existed": result.existed,
                        "path": result.path,
                        "last_date": sink.last_date(normalized),
                    }
            return out

        raise ValidationError(
            "unknown composed capability",
            context={"capability": capability},
        )

    def _tdx_quotes(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            return client.quotes(list(plan.spec.symbols))

    def _tdx_bars(self, plan: QueryPlan) -> Any:
        if plan.spec.adjustment:
            raise ValidationError(
                "TDX Direct bars 不支持在原始 bars 调用中静默复权",
                context={
                    "provider": "tdx",
                    "channel": "quotation",
                    "adjustment": plan.spec.adjustment,
                },
            )
        with self._tdx_client() as client:
            return client.bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                start=plan.spec.start,
            )

    def _tdx_snapshot(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            return client.snapshot(plan.spec.symbols[0], as_format="dict")

    def _tdx_minute(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            return client.minute_today(plan.spec.symbols[0])

    def _tdx_trades(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            return client.trade_today(
                plan.spec.symbols[0],
                start=plan.spec.start,
                count=plan.spec.count,
            )

    def _tdx_security_count(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            market = 0 if getattr(plan.spec, "market", None) is None else plan.spec.market  # type: ignore[attr-defined]
            return client.security_count(market)

    def _tdx_security_list(self, plan: QueryPlan) -> Any:
        with self._tdx_client() as client:
            market = 0 if getattr(plan.spec, "market", None) is None else plan.spec.market  # type: ignore[attr-defined]
            return client.security_list(market, plan.spec.start)

    def _local_bars(self, plan: QueryPlan) -> Any:
        if not self.vipdoc_root:
            raise ValidationError(
                "local_vipdoc Provider 需要显式 vipdoc_root",
                context={"provider": "local_vipdoc", "channel": "vipdoc"},
            )
        if plan.spec.adjustment:
            raise ValidationError(
                "local_vipdoc Direct bars 不支持静默复权",
                context={
                    "provider": "local_vipdoc",
                    "channel": "vipdoc",
                    "adjustment": plan.spec.adjustment,
                },
            )

        from .domain.symbol import split_symbol
        from .reader import DayBarReader, MinBarReader

        market, code = split_symbol(plan.spec.symbols[0])
        if market not in {"sh", "sz", "bj"}:
            raise ValidationError("local_vipdoc Direct bars 仅支持沪深北本地目录")

        root = Path(self.vipdoc_root)
        canonical = f"{market}{code}"
        if plan.spec.period == "day":
            rows = DayBarReader().read(
                root / market / "lday" / f"{canonical}.day",
                output="model",
            )
        elif plan.spec.period == "1min":
            rows = MinBarReader(profile="a_share_min", interval=1).read(
                root / market / "minline" / f"{canonical}.lc1",
                output="model",
            )
        elif plan.spec.period == "5min":
            rows = MinBarReader(profile="a_share_min", interval=5).read(
                root / market / "fzline" / f"{canonical}.lc5",
                output="model",
            )
        else:
            raise ValidationError("local_vipdoc Direct bars 仅支持 day/1min/5min")

        if plan.spec.start:
            end = max(0, len(rows) - plan.spec.start)
            begin = max(0, end - plan.spec.count) if plan.spec.count else 0
            return rows[begin:end]
        return rows[-plan.spec.count:] if plan.spec.count else rows

    def _web_quotes(self, plan: QueryPlan) -> Any:
        from .web import get_quotes

        return get_quotes(
            list(plan.spec.symbols),
            source=plan.provider,
            timeout=self.timeout,
        )

    def _tencent_bars(self, plan: QueryPlan) -> Any:
        from .web import create_source

        source_name = "minute_kline" if plan.channel == "minute_kline" else "kline"
        src = create_source(source_name, timeout=self.timeout)
        try:
            if plan.channel == "minute_kline":
                if plan.spec.adjustment:
                    raise ValidationError("Tencent minute_kline 不支持复权参数")
                return src.fetch_bars(  # type: ignore[attr-defined]
                    plan.spec.symbols[0],
                    period=plan.spec.period,
                    count=plan.spec.count,
                )
            return src.fetch_bars(  # type: ignore[attr-defined]
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
            raise ValidationError("Baidu Direct bars 不支持复权参数")
        src = BaiduSource(timeout=self.timeout)
        try:
            return src.fetch_kline(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
            )
        finally:
            src.close()
