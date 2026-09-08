# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-bound high-level market-data service.

The service is the executable v12 boundary:

``Provider -> Channel -> Capability -> Endpoint/Host``.

One production query binds to exactly one Provider. TDX host failover remains
inside TDX transport pools; no error in this module triggers a request to a
different Provider. Live quote paths are direct upstream fetches only: stale
cache, replay and synthetic data are not consulted by this service.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, NoReturn, TypeVar

from .domain.models import Bar, Quote
from .domain.symbol import normalize_symbol
from .errors import AllHostsUnreachable, SourceUnavailable, ValidationError
from .providers import PROVIDERS, ProviderSpec, resolve_provider

if TYPE_CHECKING:
    from .provider_api import ProviderAPI

__all__ = [
    "FreshnessEvidence",
    "ResultMeta",
    "QueryResult",
    "ProviderManager",
    "UnifiedMarketDataService",
    "market_data",
]

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class FreshnessEvidence:
    """Evidence that describes how the returned data was obtained.

    ``origin='direct'`` is mandatory for live unified queries. Provider timestamps
    are preserved when the upstream exposes one; TDX 0x0530 currently has no
    verified wall-clock timestamp, so ``provider_timestamp`` may legitimately be
    ``None`` while the direct-fetch evidence remains explicit.
    """

    origin: str
    observed_at_ns: int
    provider_timestamp: str | None = None
    cache_hit: bool = False
    replay: bool = False
    synthetic: bool = False

    @property
    def real(self) -> bool:
        return self.origin == "direct" and not self.replay and not self.synthetic


@dataclass(frozen=True, slots=True)
class ResultMeta:
    provider: str
    channel: str
    capability: str
    observed_at_ns: int
    freshness: FreshnessEvidence
    real: bool = True
    fallback: bool = False

    @property
    def source(self) -> str:
        """Compatibility provenance alias; formal entity remains Provider."""
        return self.provider


@dataclass(frozen=True, slots=True)
class QueryResult(Generic[T]):
    data: T
    meta: ResultMeta


class ProviderManager:
    """Own and reuse all Provider/Channel client lifecycles."""

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.hosts = list(hosts) if hosts is not None else None
        self.timeout = float(timeout)
        self._lock = threading.RLock()
        self._tdx_clients: dict[str, Any] = {}
        self._adapters: dict[tuple[str, str], Any] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ProviderManager 已关闭")

    def tdx_channel(self, channel: str) -> Any:
        """Return one persistent TDX channel client.

        Each protocol family owns its own pool; only ``quotation`` consumes the
        user-supplied 7709 host list. Other channels resolve their own family
        hosts, so a 7709 override cannot accidentally leak into GOODS/F10/7727.
        """
        cid = str(channel).strip().lower()
        PROVIDERS.get("tdx").channel(cid)
        with self._lock:
            self._ensure_open()
            existing = self._tdx_clients.get(cid)
            if existing is not None:
                return existing

            from .client import ExMarketClient, F10Client, GoodsClient, MacClient, TdxClient

            if cid == "quotation":
                kwargs: dict[str, Any] = {"timeout": self.timeout}
                if self.hosts is not None:
                    kwargs["hosts"] = self.hosts
                client = TdxClient(**kwargs)
            elif cid == "extended":
                client = ExMarketClient(timeout=self.timeout)
            elif cid == "goods":
                client = GoodsClient(timeout=self.timeout)
            elif cid == "f10":
                client = F10Client(timeout=self.timeout)
            elif cid == "mac":
                client = MacClient(timeout=self.timeout)
            else:
                raise ValidationError(
                    f"TDX channel {cid!r} 不是在线客户端 channel",
                    context={"provider": "tdx", "channel": cid},
                )
            self._tdx_clients[cid] = client
            return client

    @property
    def tdx(self) -> Any:
        return self.tdx_channel("quotation")

    def adapter(
        self,
        provider: str,
        channel: str,
        factory: Callable[[], Any],
    ) -> Any:
        pid = resolve_provider(provider=provider)
        PROVIDERS.get(pid).channel(channel)
        key = (pid, channel)
        with self._lock:
            self._ensure_open()
            existing = self._adapters.get(key)
            if existing is not None:
                return existing
            obj = factory()
            self._adapters[key] = obj
            return obj

    def quote_adapter(self, provider: str) -> Any:
        pid = resolve_provider(provider=provider)
        if pid == "tencent":
            from .web.adapters import TencentSource

            return self.adapter(pid, "quote", lambda: TencentSource(timeout=self.timeout))
        if pid == "sina":
            from .web.adapters import SinaSource

            return self.adapter(pid, "quote", lambda: SinaSource(timeout=self.timeout))
        if pid == "eastmoney":
            from .web.adapters import EastmoneySource

            return self.adapter(pid, "quote", lambda: EastmoneySource(timeout=self.timeout))
        if pid == "baidu":
            from .web.adapters_baidu import BaiduSource

            return self.adapter(pid, "quote", lambda: BaiduSource(timeout=self.timeout))
        raise ValidationError(
            f"Provider {pid!r} 尚无统一 quotes adapter",
            context={"provider": pid, "capability": "quotes"},
        )

    def bar_adapter(self, provider: str, *, period: str = "day") -> tuple[str, Any]:
        pid = resolve_provider(provider=provider)
        p = (period or "day").strip().lower()
        minute_periods = {"1min", "1m", "min", "5min", "5m", "15min", "15m", "30min", "30m", "60min", "60m"}
        if pid == "tencent":
            from .web.adapters import KlineSource

            channel = "minute_kline" if p in minute_periods else "kline"
            return channel, self.adapter(
                pid,
                channel,
                lambda: KlineSource(timeout=self.timeout),
            )
        if pid == "sina":
            from .web.history import SinaHistoryKlineSource

            return "history_kline", self.adapter(
                pid,
                "history_kline",
                lambda: SinaHistoryKlineSource(timeout=self.timeout),
            )
        if pid == "eastmoney":
            from .web.history import EastmoneyHistoryKlineSource

            return "kline", self.adapter(
                pid,
                "kline",
                lambda: EastmoneyHistoryKlineSource(timeout=self.timeout),
            )
        if pid == "baidu":
            from .web.adapters_baidu import BaiduSource

            return "kline", self.adapter(
                pid,
                "kline",
                lambda: BaiduSource(timeout=self.timeout),
            )
        raise ValidationError(
            f"Provider {pid!r} 尚无统一 bars adapter",
            context={"provider": pid, "capability": "bars"},
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            objects = [*self._tdx_clients.values(), *self._adapters.values()]
            seen: set[int] = set()
            for obj in objects:
                if id(obj) in seen:
                    continue
                seen.add(id(obj))
                with contextlib.suppress(Exception):
                    obj.close()
            self._tdx_clients.clear()
            self._adapters.clear()
            self._closed = True


class UnifiedMarketDataService:
    """TDX-primary unified service with explicit Provider selection."""

    def __init__(
        self,
        *,
        hosts: Sequence[Any] | None = None,
        timeout: float = 5.0,
        manager: ProviderManager | None = None,
    ) -> None:
        self.manager = manager or ProviderManager(hosts=hosts, timeout=timeout)
        self._namespaces: dict[str, ProviderAPI] = {}

    def _provider(self, *, provider: str | None, source: str | None, capability: str) -> str:
        pid = resolve_provider(
            provider=provider,
            source=source,
            default=PROVIDERS.default_provider,
        )
        PROVIDERS.require(pid, capability)
        return pid

    @staticmethod
    def _provider_timestamp(items: Sequence[Any]) -> str | None:
        if not items:
            return None
        item = items[-1]
        dt = getattr(item, "datetime", None)
        if dt:
            return str(dt)
        extra = getattr(item, "extra", None)
        if isinstance(extra, dict):
            date = extra.get("date")
            stamp = extra.get("time") or extra.get("datetime")
            if date and stamp:
                return f"{date} {stamp}"
            if stamp:
                return str(stamp)
        return None

    @classmethod
    def _meta(
        cls,
        provider: str,
        channel: str,
        capability: str,
        data: Sequence[Any],
    ) -> ResultMeta:
        observed = time.time_ns()
        freshness = FreshnessEvidence(
            origin="direct",
            observed_at_ns=observed,
            provider_timestamp=cls._provider_timestamp(data),
        )
        if not freshness.real:
            raise AssertionError("production market-data service returned non-real origin")
        return ResultMeta(
            provider=provider,
            channel=channel,
            capability=capability,
            observed_at_ns=observed,
            freshness=freshness,
            real=True,
            fallback=False,
        )

    @staticmethod
    def _raise_provider_unavailable(
        provider: str,
        capability: str,
        exc: BaseException,
        *,
        channel: str | None = None,
    ) -> NoReturn:
        raise SourceUnavailable(
            f"Provider {provider!r} 当前不可用，未切换其它 Provider",
            context={
                "provider": provider,
                "channel": channel,
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
        """Fetch current quotes directly from one selected Provider."""
        pid = self._provider(provider=provider, source=source, capability="quotes")
        seq = [symbols] if isinstance(symbols, str) else list(symbols)
        syms = [normalize_symbol(x) for x in seq]
        channel = "quotation" if pid == "tdx" else "quote"
        try:
            if pid == "tdx":
                rows = self.manager.tdx.quotes(syms, as_format="dict")
                from .client_core import _row_to_quote

                data = [_row_to_quote(row) for row in rows]
            elif pid == "baidu":
                adapter = self.manager.quote_adapter(pid)
                data = [adapter.fetch_quote(sym) for sym in syms]
            else:
                data = list(self.manager.quote_adapter(pid).fetch(syms))
        except AllHostsUnreachable as exc:
            self._raise_provider_unavailable(pid, "quotes", exc, channel=channel)

        if syms and not data:
            raise SourceUnavailable(
                f"Provider {pid!r} 返回空实时行情",
                context={
                    "provider": pid,
                    "channel": channel,
                    "capability": "quotes",
                    "fallback": False,
                },
            )
        if with_meta:
            return QueryResult(data=data, meta=self._meta(pid, channel, "quotes", data))
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
        """Fetch bars from one Provider without semantic/provider switching."""
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

        channel = "quotation"
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
                channel, adapter = self.manager.bar_adapter(pid, period=period)
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
                    if adjust:
                        raise ValidationError(
                            "Baidu K 线不接受统一复权参数；不会切换其它 Provider",
                            context={"provider": pid, "capability": "bars", "adjust": adjust},
                        )
                    data = list(adapter.fetch_kline(sym, period=period, count=count))
                else:
                    raise ValidationError(f"Provider {pid!r} 尚无 bars adapter")
        except AllHostsUnreachable as exc:
            self._raise_provider_unavailable(pid, "bars", exc, channel=channel)

        if not data:
            raise SourceUnavailable(
                f"Provider {pid!r} 返回空 K 线",
                context={
                    "provider": pid,
                    "channel": channel,
                    "capability": "bars",
                    "fallback": False,
                },
            )
        if with_meta:
            return QueryResult(data=data, meta=self._meta(pid, channel, "bars", data))
        return data

    def provider(self, provider: str) -> ProviderAPI:
        pid = resolve_provider(provider=provider)
        PROVIDERS.get(pid)
        namespace = self._namespaces.get(pid)
        if namespace is None:
            from .provider_api import build_provider_api

            namespace = build_provider_api(self, pid)
            self._namespaces[pid] = namespace
        return namespace

    @property
    def tdx(self) -> ProviderAPI:
        return self.provider("tdx")

    @property
    def tencent(self) -> ProviderAPI:
        return self.provider("tencent")

    @property
    def sina(self) -> ProviderAPI:
        return self.provider("sina")

    @property
    def eastmoney(self) -> ProviderAPI:
        return self.provider("eastmoney")

    @property
    def baidu(self) -> ProviderAPI:
        return self.provider("baidu")

    @property
    def jsl(self) -> ProviderAPI:
        return self.provider("jsl")

    @property
    def boc(self) -> ProviderAPI:
        return self.provider("boc")

    @property
    def iwencai(self) -> ProviderAPI:
        return self.provider("iwencai")

    def close(self) -> None:
        self.manager.close()

    def __enter__(self) -> "UnifiedMarketDataService":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def market_data(**kwargs: Any) -> UnifiedMarketDataService:
    return UnifiedMarketDataService(**kwargs)
