# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-bound compatibility facade.

The historical :mod:`tstdx.facade.api` class remains importable for callers that
need the exact pre-v12 surface.  The official :mod:`tstdx.facade` export points
to this strict subclass, which keeps the same method names while enforcing the
v12 Provider contract:

* ``route='auto'`` means TDX for capabilities that TDX implements;
* TDX failure never calls Sina/Tencent/Eastmoney;
* Web/local paths require an explicit route/default selection;
* unsupported semantics (for example TDX qfq/hfq) fail rather than changing
  Provider;
* an explicitly supplied empty corporate-action list is a real input, not a
  signal to perform another network request;
* the legacy router and direct TDX calls share one ProviderManager lifecycle.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

from ..errors import ValidationError
from ..service import UnifiedMarketDataService
from .api import UnifiedQuoteAPI as LegacyUnifiedQuoteAPI
from .routing import Route

__all__ = ["UnifiedQuoteAPI", "LegacyUnifiedQuoteAPI", "quote_api"]


class UnifiedQuoteAPI(LegacyUnifiedQuoteAPI):
    """Legacy-shaped facade backed by strict Provider selection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._provider_service: UnifiedMarketDataService | None = None

    def _get_provider_service(self) -> UnifiedMarketDataService:
        if self._provider_service is None:
            self._provider_service = UnifiedMarketDataService(
                hosts=self.hosts,
                timeout=self.timeout,
            )
        return self._provider_service

    @property
    def tdx(self) -> Any:
        """Reuse the strict service's persistent TDX quotation client."""
        return self._get_provider_service().manager.tdx

    def _get_router(self) -> Any:
        """Reuse the same service for legacy quotes/bars compatibility calls."""
        if self._router is None:
            from ..sources import DataSourceRouter

            self._router = DataSourceRouter(
                vipdoc_root=self.vipdoc_root,
                golden_root=self.golden_root,
                tdx_hosts=self.hosts,
                web_sources=self.web_sources,
                timeout=self.timeout,
                service=self._get_provider_service(),
            )
        return self._router

    def _resolved_route(self, route: Route | None) -> Route:
        return route or self.default_route

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str = "",
        route: Route | None = None,
    ) -> list[Any]:
        """Keep TDX as the default Provider even when semantics are unsupported.

        The legacy implementation removed TDX from the candidate map when
        ``adjust`` was requested, causing ``route='auto'`` to silently select a
        Web Provider.  v12 rejects that Provider switch.  Explicit ``route='web'``
        remains an intentional user choice for compatibility.
        """
        resolved = self._resolved_route(route)
        if resolved == "auto":
            resolved = "tdx"
        if resolved == "tdx" and adjust:
            raise ValidationError(
                "TDX quotation 只提供原始 K 线；复权请求不会自动切换其它 Provider。"
                "请显式选择 Web Provider/Direct API，或使用 adjusted_bars 本地复权链路。",
                context={
                    "provider": "tdx",
                    "capability": "bars",
                    "adjust": adjust,
                    "fallback": False,
                },
            )
        return super().bars(
            symbol,
            period=period,
            count=count,
            start=start,
            adjust=adjust,
            route=resolved,
        )

    def security_list(
        self,
        market: int | str = 1,
        start: int = 0,
        *,
        route: Route | None = None,
    ) -> list[Any]:
        """Never convert TDX ``CommandOffline`` into an Eastmoney request."""
        resolved = self._resolved_route(route)
        if resolved == "auto":
            resolved = "tdx"
        if resolved == "web":
            return self._security_list_web(market, start)
        if resolved != "tdx":
            raise ValidationError(
                "security_list 仅支持显式 tdx 或 web Provider 路径",
                context={"route": resolved, "fallback": False},
            )
        # Let CommandOffline/AllHostsUnreachable propagate.  This is deliberate:
        # the selected Provider is TDX and there is no hidden Provider switch.
        return list(self.tdx.security_list(market, start))

    def security_list_all(
        self,
        market: int | str = 1,
        *,
        route: Route | None = None,
    ) -> list[Any]:
        """Fetch all pages from one explicitly selected Provider only."""
        resolved = self._resolved_route(route)
        if resolved == "auto":
            resolved = "tdx"
        if resolved == "web":
            return self._security_list_web(market, 0)
        if resolved != "tdx":
            raise ValidationError(
                "security_list_all 仅支持显式 tdx 或 web Provider 路径",
                context={"route": resolved, "fallback": False},
            )
        return list(self.tdx.security_list_all(market))

    def adjusted_bars(
        self,
        symbol: str,
        *,
        method: str = "qfq",
        period: str = "day",
        count: int = 320,
        start: int = 0,
        events: Sequence[Any] | None = None,
        anchor_date: str | None = None,
    ) -> list[Any]:
        """Apply adjustment while respecting an explicitly empty event list."""
        from ..domain.adjust import AdjustEngine
        from ..domain.finance import to_capital_changes
        from ..domain.models import CapitalChange
        from ..domain.symbol import normalize_symbol

        sym = normalize_symbol(symbol)
        bars = self.bars(
            sym,
            period=period,
            count=count,
            start=start,
            route="local",
        )
        # ``[]`` means "there are no actions" and MUST NOT cause a hidden
        # capital_changes network query. Only ``None`` means "load events".
        ev_list = (
            list(events)
            if events is not None
            else list(self.capital_changes(sym, route="tdx"))
        )
        if ev_list and not isinstance(ev_list[0], CapitalChange):
            ev_list = to_capital_changes(ev_list)
        return AdjustEngine().apply(
            list(bars),
            ev_list,
            method,
            anchor_date=anchor_date,
        )

    def close(self) -> None:
        """Close the single Provider service plus legacy explicit-Web resources."""
        # Router uses the external service and therefore does not own/close it.
        router = getattr(self, "_router", None)
        if router is not None:
            with contextlib.suppress(Exception):
                router.close()
        self._router = None

        web = getattr(self, "_web", None)
        if web is not None:
            with contextlib.suppress(Exception):
                web.close()
        self._web = None
        self._tdx = None  # never owned in this strict subclass

        service = self._provider_service
        if service is not None:
            with contextlib.suppress(Exception):
                service.close()
        self._provider_service = None


def quote_api(**kwargs: Any) -> UnifiedQuoteAPI:
    return UnifiedQuoteAPI(**kwargs)
