# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Compatibility facade backed by the Provider-first runtime.

Only the canonical ``quotes``/``bars`` paths are overridden here.  The large
legacy facade remains the compatibility implementation for auxiliary methods,
while its core market-data paths now delegate to :class:`UnifiedRuntime` and
explicit :class:`ProviderOrchestrator` policies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.models import Bar, Quote
from ..domain.symbol import normalize_symbol
from ..errors import ValidationError
from ..orchestration import FallbackPolicy, ProviderOrchestrator
from ..providers import PROVIDERS
from ..runtime import UnifiedRuntime
from .api import UnifiedQuoteAPI as _LegacyUnifiedQuoteAPI
from .api import _as_bar, _as_quote
from .routing import Route

__all__ = ["UnifiedQuoteAPI", "quote_api"]

_DEFAULT_WEB_PROVIDERS: tuple[str, ...] = ("tencent", "sina", "eastmoney", "baidu")


def quote_api(**kwargs: Any) -> "UnifiedQuoteAPI":
    return UnifiedQuoteAPI(**kwargs)


class UnifiedQuoteAPI(_LegacyUnifiedQuoteAPI):
    """Legacy facade surface with canonical quotes/bars delegated to v12 runtime.

    ``route='auto'`` is compatibility orchestration only.  Each individual
    attempt still executes one exact Provider/Channel through ``UnifiedRuntime``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._runtime_v12 = UnifiedRuntime(
            timeout=self.timeout,
            hosts=self.hosts,
            vipdoc_root=self.vipdoc_root,
        )
        self._orchestrator_v12 = ProviderOrchestrator(self._runtime_v12)

    def _web_providers(self, capability: str) -> tuple[str, ...]:
        requested = tuple(self.web_sources or ())
        candidates = requested or _DEFAULT_WEB_PROVIDERS
        out: list[str] = []
        for provider in candidates:
            normalized = str(provider).strip().lower()
            if normalized not in PROVIDERS.ids() or normalized in {"tdx", "local_vipdoc"}:
                continue
            try:
                PROVIDERS.require(normalized, capability)
            except Exception:
                continue
            if normalized not in out:
                out.append(normalized)
        if not out:
            raise ValidationError(
                "兼容 web 路由没有可执行 Provider",
                context={"capability": capability},
            )
        return tuple(out)

    @staticmethod
    def _quotes_data(data: Any) -> list[Quote]:
        return [item if isinstance(item, Quote) else _as_quote(item) for item in list(data)]

    @staticmethod
    def _bars_data(data: Any) -> list[Bar]:
        return [item if isinstance(item, Bar) else _as_bar(item) for item in list(data)]

    def quotes(self, symbols: str | Sequence[str], *, route: Route | None = None) -> list[Quote]:
        syms = [normalize_symbol(symbols)] if isinstance(symbols, str) else [
            normalize_symbol(item) for item in symbols
        ]
        resolved: Route = route or self.default_route
        if resolved == "local":
            raise ValueError("quotes(route='local') 无本地实时行情 Provider")
        if resolved == "tdx":
            return self._quotes_data(self._runtime_v12.quotes(syms, provider="tdx").data)
        if resolved == "web":
            policy = FallbackPolicy.build(*self._web_providers("quotes"))
            return self._quotes_data(
                self._orchestrator_v12.quotes(syms, policy=policy).result.data
            )
        if resolved != "auto":
            raise ValueError(f"未知 route: {resolved!r}")
        policy = FallbackPolicy.build("tdx", *self._web_providers("quotes"))
        return self._quotes_data(self._orchestrator_v12.quotes(syms, policy=policy).result.data)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str | None = None,
        route: Route | None = None,
    ) -> list[Bar]:
        sym = normalize_symbol(symbol)
        adjustment = adjust or ""
        resolved: Route = route or self.default_route

        if resolved == "local":
            result = self._runtime_v12.bars(
                sym,
                provider="local_vipdoc",
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
            )
            return self._bars_data(result.data)
        if resolved == "tdx":
            result = self._runtime_v12.bars(
                sym,
                provider="tdx",
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
            )
            return self._bars_data(result.data)
        if resolved == "web":
            if start:
                raise ValueError(
                    f"bars(route='web') 不支持 start={start!r}：拒绝改变数据窗口"
                )
            policy = FallbackPolicy.build(*self._web_providers("bars"))
            result = self._orchestrator_v12.bars(
                sym,
                policy=policy,
                period=period,
                count=count,
                adjustment=adjustment,
            ).result
            return self._bars_data(result.data)
        if resolved != "auto":
            raise ValueError(f"未知 route: {resolved!r}")

        providers: list[str] = []
        if adjustment:
            # Native Providers only expose raw bars; adjusted requests must use
            # an explicitly capable Web Provider.
            if start:
                raise ValidationError(
                    "bars auto 无 Provider 同时满足 start 与 adjustment",
                    context={"start": start, "adjustment": adjustment},
                )
            providers.extend(self._web_providers("bars"))
        else:
            if self.vipdoc_root:
                providers.append("local_vipdoc")
            providers.append("tdx")
            if not start:
                providers.extend(self._web_providers("bars"))

        policy = FallbackPolicy.build(*providers)
        result = self._orchestrator_v12.bars(
            sym,
            policy=policy,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
        ).result
        return self._bars_data(result.data)

    def close(self) -> None:
        self._runtime_v12.close()
        super().close()
