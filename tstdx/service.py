# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-bound high-level market-data service.

This module is the first executable slice of the v12 architecture.  It does not
replace low-level ``TdxClient`` or provider adapters; it owns them and enforces
one invariant: one production query -> one Provider.

TDX host failover remains inside the TDX transport.  A TDX failure is surfaced
as TDX failure and never causes a Sina/Tencent/Eastmoney request.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .domain.models import Bar, Quote
from .domain.symbol import normalize_symbol
from .errors import AllHostsUnreachable, SourceUnavailable, ValidationError
from .providers import PROVIDERS, ProviderSpec, resolve_provider

__all__ = [
    "ResultMeta",
    "QueryResult",
    "ProviderManager",
    "BoundProvider",
    "TdxQuotationChannel",
    "TdxProvider",
    "UnifiedMarketDataService",
    "market_data",
]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ResultMeta:
    """Minimal provenance contract for the first v12 execution slice."""

    provider: str
    channel: str
    capability: str
    observed_at_ns: int
    real: bool = True
    fallback: bool = False


@dataclass(frozen=True, slots=True)
class QueryResult(Generic[T]):
    data: T
    meta: ResultMeta


class ProviderManager:
    """Own provider lifecycles and reuse live connections/sessions."""

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.hosts = list(hosts) if hosts is not None else None
        self.timeout = float(timeout)
        self._lock = threading.RLock()
        self._tdx: Any = None
        self._web_sessions: dict[str, Any] = {}
        self._bar_adapters: dict[str, Any] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ProviderManager 已关闭")

    @property
    def tdx(self) -> Any:
        with self._lock:
            self._ensure_open()
            if self._tdx is None:
                from .client import TdxClient

                kwargs: dict[str, Any] = {"timeout": self.timeout}
                if self.hosts is not None:
                    kwargs["hosts"] = self.hosts
                self._tdx = TdxClient(**kwargs)
            return self._tdx

    def web_session(self, provider: str) -> Any:
        """Return a persistent session bound to exactly one Web provider."""
        pid = resolve_provider(provider=provider)
        if pid == "tdx":
            raise ValidationError("tdx 不是 HTTP Web Provider")
        with self._lock:
            self._ensure_open()
            session = self._web_sessions.get(pid)
            if session is None:
                from .web.facade import web_session

                session = web_session(pid, timeout=self.timeout)
                self._web_sessions[pid] = session
            return session

    def bar_adapter(self, provider: str) -> Any:
        """Return a persistent provider-specific bar adapter.

        Generic ``WebQuoteSession.klines`` is deliberately not used because some
        legacy paths switch provider based on market/adjustment.  This method
        binds one concrete adapter to one Provider.
        """
        pid = resolve_provider(provider=provider)
        with self._lock:
            self._ensure_open()
            adapter = self._bar_adapters.get(pid)
            if adapter is not None:
                return adapter
            if pid == "tencent":
                from .web.adapters import KlineSource

                adapter = KlineSource()
            elif pid == "sina":
                from .web.history import SinaHistoryKlineSource

                adapter = SinaHistoryKlineSource()
            elif pid == "eastmoney":
                from .web.history import EastmoneyHistoryKlineSource

                adapter = EastmoneyHistoryKlineSource()
            elif pid == "baidu":
                from .web.adapters_baidu import BaiduSource

                adapter = BaiduSource()
            else:
                raise ValidationError(
                    f"Provider {pid!r} 尚无 bars adapter",
                    context={"provider": pid, "capability": "bars"},
                )
            self._bar_adapters[pid] = adapter
            return adapter

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            objects = [self._tdx, *self._web_sessions.values(), *self._bar_adapters.values()]
            seen: set[int] = set()
            for obj in objects:
                if obj is None or id(obj) in seen:
                    continue
                seen.add(id(obj))
                with contextlib.suppress(Exception):
                    obj.close()
            self._tdx = None
            self._web_sessions.clear()
            self._bar_adapters.clear()
            self._closed = True


class BoundProvider:
    """Direct namespace bound to one ProviderId."""

    def __init__(self, service: "UnifiedMarketDataService", provider: str) -> None:
        self._service = service
        self.provider = resolve_provider(provider=provider)
        PROVIDERS.get(self.provider)

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDERS.get(self.provider)

    def quotes(self, symbols: str | Sequence[str], *, with_meta: bool = False) -> Any:
        return self._service.quotes(symbols, provider=self.provider, with_meta=with_meta)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str = "",
        with_meta: bool = False,
    ) -> Any:
        return self._service.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            adjust=adjust,
            provider=self.provider,
            with_meta=with_meta,
        )

    @property
    def session(self) -> Any:
        """Bound legacy Web session for provider-specific capabilities.

        This is an explicit escape hatch during migration; it never participates
        in unified-query fallback.  Provider-specific first-class channel
        namespaces are added incrementally on top of the same manager.
        """
        if self.provider == "tdx":
            return self._service.manager.tdx
        return self._service.manager.web_session(self.provider)


class TdxQuotationChannel:
    """Explicit ``md.tdx.quotation`` channel."""

    def __init__(self, service: "UnifiedMarketDataService") -> None:
        self._service = service

    def quotes(self, symbols: str | Sequence[str], *, with_meta: bool = False) -> Any:
        return self._service.quotes(symbols, provider="tdx", with_meta=with_meta)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        with_meta: bool = False,
    ) -> Any:
        return self._service.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            provider="tdx",
            with_meta=with_meta,
        )

    def minute(self, symbol: str) -> list[Any]:
        sym = normalize_symbol(symbol)
        return list(self._service.manager.tdx.minute_today(sym))

    def trades(self, symbol: str, *, start: int = 0, count: int = 0) -> list[Any]:
        sym = normalize_symbol(symbol)
        return list(self._service.manager.tdx.trade_today(sym, start=start, count=count))

    def finance(self, symbol: str) -> dict[str, Any]:
        return dict(self._service.manager.tdx.finance_info(normalize_symbol(symbol)))

    def capital_changes(self, symbol: str) -> list[Any]:
        return list(self._service.manager.tdx.capital_changes(normalize_symbol(symbol)))


class TdxProvider(BoundProvider):
    def __init__(self, service: "UnifiedMarketDataService") -> None:
        super().__init__(service, "tdx")
        self.quotation = TdxQuotationChannel(service)

    @property
    def raw(self) -> Any:
        """Low-level TDX client; retains TDX-internal host failover."""
        return self._service.manager.tdx


class UnifiedMarketDataService:
    """TDX-primary unified service with explicit provider selection."""

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
        manager: ProviderManager | None = None,
    ) -> None:
        self.manager = manager or ProviderManager(hosts=hosts, timeout=timeout)
        self._namespaces: dict[str, BoundProvider] = {}

    def _provider(self, *, provider: str | None, source: str | None, capability: str) -> str:
        pid = resolve_provider(
            provider=provider,
            source=source,
            default=PROVIDERS.default_provider,
        )
        PROVIDERS.require(pid, capability)
        return pid

    @staticmethod
    def _channel_for(provider: str, capability: str) -> str:
        channels = PROVIDERS.get(provider).channels_for(capability)
        if not channels:
            raise ValidationError(
                f"Provider {provider!r} 不支持 capability {capability!r}",
                context={"provider": provider, "capability": capability},
            )
        return channels[0].id

    @staticmethod
    def _meta(provider: str, capability: str) -> ResultMeta:
        return ResultMeta(
            provider=provider,
            channel=UnifiedMarketDataService._channel_for(provider, capability),
            capability=capability,
            observed_at_ns=time.time_ns(),
            real=True,
            fallback=False,
        )

    @staticmethod
    def _raise_provider_unavailable(
        provider: str,
        capability: str,
        exc: BaseException,
    ) -> None:
        raise SourceUnavailable(
            f"Provider {provider!r} 当前不可用，未切换其它 Provider",
            context={
                "provider": provider,
                "capability": capability,
                "cause": type(exc).__name__,
                "fallback": False,
            },
            cause=exc,
        ) from exc

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        source: str | None = None,
        with_meta: bool = False,
    ) -> list[Quote] | QueryResult[list[Quote]]:
        pid = self._provider(provider=provider, source=source, capability="quotes")
        seq = [symbols] if isinstance(symbols, str) else list(symbols)
        syms = [normalize_symbol(x) for x in seq]
        try:
            if pid == "tdx":
                rows = self.manager.tdx.quotes(syms, as_format="dict")
                from .client_core import _row_to_quote

                data = [_row_to_quote(row) for row in rows]
            else:
                data = list(self.manager.web_session(pid).quotes(syms))
        except AllHostsUnreachable as exc:
            self._raise_provider_unavailable(pid, "quotes", exc)
        if syms and not data:
            raise SourceUnavailable(
                f"Provider {pid!r} 返回空实时行情",
                context={"provider": pid, "capability": "quotes", "fallback": False},
            )
        if with_meta:
            return QueryResult(data=data, meta=self._meta(pid, "quotes"))
        return data

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str = "",
        provider: str | None = None,
        source: str | None = None,
        with_meta: bool = False,
    ) -> list[Bar] | QueryResult[list[Bar]]:
        pid = self._provider(provider=provider, source=source, capability="bars")
        sym = normalize_symbol(symbol)
        if pid != "tdx" and start:
            raise ValidationError(
                f"Provider {pid!r} bars 当前不支持 start={start!r}；拒绝静默改变窗口",
                context={"provider": pid, "capability": "bars", "start": start},
            )
        if pid == "tdx" and adjust:
            raise ValidationError(
                "TDX quotation 仅提供原始价；复权请求不会自动切换 Web Provider",
                context={"provider": "tdx", "capability": "bars", "adjust": adjust},
            )
        if pid == "sina" and adjust:
            raise ValidationError(
                "Sina history_kline 仅提供原始价；复权请求不会自动切换 Eastmoney",
                context={"provider": "sina", "capability": "bars", "adjust": adjust},
            )

        try:
            if pid == "tdx":
                rows = self.manager.tdx.bars(
                    sym,
                    period=period,
                    count=count,
                    start=start,
                    as_format="dict",
                )
                from .client_core import _row_to_bar

                data = [_row_to_bar(row) for row in rows]
            else:
                adapter = self.manager.bar_adapter(pid)
                if pid == "tencent":
                    data = list(
                        adapter.fetch_bars(sym, period=period, count=count, adjust=adjust)
                    )
                elif pid == "sina":
                    data = list(adapter.fetch_bars(sym, period=period, count=count, adjust=""))
                elif pid == "eastmoney":
                    data = list(
                        adapter.fetch_bars(sym, period=period, count=count, adjust=adjust)
                    )
                elif pid == "baidu":
                    fetch = getattr(adapter, "fetch_bars", None)
                    if fetch is None:
                        raise ValidationError("Baidu adapter 当前未暴露 provider-bound bars API")
                    data = list(fetch(sym, period=period, count=count))
                else:  # guarded by ProviderManager but keeps type narrowing explicit
                    raise ValidationError(f"Provider {pid!r} 尚无 bars adapter")
        except AllHostsUnreachable as exc:
            self._raise_provider_unavailable(pid, "bars", exc)

        if not data:
            raise SourceUnavailable(
                f"Provider {pid!r} 返回空 K 线",
                context={"provider": pid, "capability": "bars", "fallback": False},
            )
        if with_meta:
            return QueryResult(data=data, meta=self._meta(pid, "bars"))
        return data

    def provider(self, provider: str) -> BoundProvider:
        pid = resolve_provider(provider=provider)
        PROVIDERS.get(pid)
        if pid not in self._namespaces:
            self._namespaces[pid] = TdxProvider(self) if pid == "tdx" else BoundProvider(self, pid)
        return self._namespaces[pid]

    @property
    def tdx(self) -> TdxProvider:
        return self.provider("tdx")  # type: ignore[return-value]

    @property
    def tencent(self) -> BoundProvider:
        return self.provider("tencent")

    @property
    def sina(self) -> BoundProvider:
        return self.provider("sina")

    @property
    def eastmoney(self) -> BoundProvider:
        return self.provider("eastmoney")

    @property
    def baidu(self) -> BoundProvider:
        return self.provider("baidu")

    @property
    def jsl(self) -> BoundProvider:
        return self.provider("jsl")

    @property
    def boc(self) -> BoundProvider:
        return self.provider("boc")

    @property
    def iwencai(self) -> BoundProvider:
        return self.provider("iwencai")

    def close(self) -> None:
        self.manager.close()

    def __enter__(self) -> "UnifiedMarketDataService":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def market_data(**kwargs: Any) -> UnifiedMarketDataService:
    return UnifiedMarketDataService(**kwargs)
