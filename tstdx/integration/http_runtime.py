# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-aware compatibility client for the existing FastAPI surface.

The HTTP router historically defaulted to a lazy bare ``TdxClient`` and therefore
bypassed the unified Provider lifecycle. ``ProviderHttpClient`` keeps the old
method names expected by ``http_server.py`` but owns one planned
:class:`UnifiedMarketDataService` underneath.

All default market-data methods remain explicitly bound to Provider ``tdx``.
TDX command/host failure is returned as-is; this adapter never switches to a Web
Provider. Provider-specific Web endpoints remain explicit HTTP endpoints during
the migration and are moved to direct Provider APIs separately.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from ..errors import ValidationError
from ..planned_service import UnifiedMarketDataService

__all__ = ["ProviderHttpClient"]


def _format_rows(rows: Sequence[Any], as_format: str) -> Any:
    if as_format == "model":
        return list(rows)
    if as_format == "dict":
        return [item.to_dict() if hasattr(item, "to_dict") else item for item in rows]
    from ..client import _emit

    return _emit(list(rows), as_format)


def _normalize_yyyymmdd(value: int | str) -> int:
    if isinstance(value, int):
        if 10_000_000 <= value <= 99_999_999:
            return value
        raise ValidationError(
            "date 必须为 YYYYMMDD",
            context={"date": value},
        )
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        raise ValidationError(
            "date 必须为 YYYYMMDD 或 YYYY-MM-DD",
            context={"date": value},
        )
    return int(text)


def _normalize_block_type(value: int | str) -> int:
    if isinstance(value, int):
        block_type = value
    else:
        text = str(value).strip().lower()
        names = {
            "concept": 0,
            "gn": 0,
            "概念": 0,
            "industry": 1,
            "hy": 1,
            "行业": 1,
            "region": 2,
            "area": 2,
            "dy": 2,
            "地区": 2,
            "index": 3,
            "zs": 3,
            "指数": 3,
        }
        if text in names:
            block_type = names[text]
        else:
            try:
                block_type = int(text)
            except ValueError as exc:
                raise ValidationError(
                    "block 必须为 concept/industry/region/index 或 0..3",
                    context={"block": value},
                ) from exc
    if block_type not in {0, 1, 2, 3}:
        raise ValidationError(
            "block_type 必须为 0..3",
            context={"block_type": block_type},
        )
    return block_type


class ProviderHttpClient:
    """Lazy old-client-shaped facade over one planned Provider-aware service."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], UnifiedMarketDataService] | None = None,
    ) -> None:
        self._service_factory = service_factory or UnifiedMarketDataService
        self._service: UnifiedMarketDataService | None = None
        self._lock = threading.RLock()
        self._closed = False

    @property
    def service(self) -> UnifiedMarketDataService:
        with self._lock:
            if self._closed:
                raise RuntimeError("ProviderHttpClient 已关闭")
            if self._service is None:
                self._service = self._service_factory()
            return self._service

    @property
    def _tdx(self) -> Any:
        return self.service.tdx

    @property
    def _quotation(self) -> Any:
        return self.service.manager.tdx_channel("quotation")

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: str = "dict",
        **_: Any,
    ) -> Any:
        rows = self.service.quotes(symbols, provider="tdx")
        if not isinstance(rows, list):
            raise RuntimeError("Provider service quotes compatibility contract violated")
        return _format_rows(rows, as_format)

    def quotes_concurrent(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: str = "dict",
        **kwargs: Any,
    ) -> Any:
        # ProviderManager/TdxClient already owns connection reuse/batching. The
        # HTTP compatibility name no longer starts another independent lifecycle.
        return self.quotes(symbols, as_format=as_format, **kwargs)

    def quotes_snapshot(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: str = "dict",
        **kwargs: Any,
    ) -> Any:
        return self.quotes(symbols, as_format=as_format, **kwargs)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
        adjust: str = "",
        **_: Any,
    ) -> Any:
        rows = self.service.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            adjust=adjust,
            provider="tdx",
        )
        if not isinstance(rows, list):
            raise RuntimeError("Provider service bars compatibility contract violated")
        return _format_rows(rows, as_format)

    @staticmethod
    def _symbols(value: str | Sequence[str]) -> list[str]:
        return [value] if isinstance(value, str) else list(value)

    def snapshot(self, symbols: str | Sequence[str]) -> Any:
        """Normalize legacy HTTP batch shape to the single-symbol TDX command."""
        return [self._quotation.snapshot(symbol) for symbol in self._symbols(symbols)]

    def auction_snapshot(self, symbols: str | Sequence[str]) -> Any:
        return [
            self._quotation.auction_snapshot(symbol)
            for symbol in self._symbols(symbols)
        ]

    def block_quotes(self, block: int | str = 0, start: int = 0) -> Any:
        return self._quotation.block_quotes(_normalize_block_type(block), start)

    def minute_today(self, symbol: str) -> Any:
        return self._quotation.minute_today(symbol)

    def minute_history(self, symbol: str, date: int | str) -> Any:
        return self._quotation.minute_history(symbol, _normalize_yyyymmdd(date))

    def trade_today(self, symbol: str, start: int = 0, count: int = 0) -> Any:
        return self._quotation.trade_today(symbol, start=start, count=count)

    def volume_price_dist(self, symbol: str) -> Any:
        return self._quotation.volume_price_dist(symbol)

    def security_list(self, market: int, start: int = 0) -> Any:
        return self._quotation.security_list(market, start)

    def security_count(self, market: int | str) -> Any:
        return self._quotation.security_count(market)

    def finance_info(self, symbol: str) -> Any:
        return self._quotation.finance_info(symbol)

    def capital_changes(self, symbol: str) -> Any:
        return self._quotation.capital_changes(symbol)

    def corporate_action(self, symbol: str) -> Any:
        # Existing public alias, same TDX quotation capability/provenance.
        return self.capital_changes(symbol)

    def file_download(self, symbol: str, filename: str) -> bytes:
        """Route F10 file download to the TDX F10 Channel."""
        return self._tdx.f10.download(symbol, filename)

    def f10_catalog(self, symbol: str) -> Any:
        return self._tdx.f10.catalog(symbol)

    def ex_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._tdx.extended.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            as_format=as_format,
        )

    def ex_quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._tdx.extended.quote(symbol, as_format=as_format)

    def goods_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._tdx.goods.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            as_format=as_format,
        )

    def goods_quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._tdx.goods.quote(symbol, as_format=as_format)

    def mac_quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._tdx.mac.quote(symbol, as_format=as_format)

    def download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int = 0,
        length: int = 0,
    ) -> bytes:
        return self._tdx.f10.download(
            symbol,
            filename,
            offset=offset,
            length=length,
        )

    def parse_text(self, *args: Any, **kwargs: Any) -> Any:
        """Parse F10 text through the same TDX F10 Channel."""
        return self._tdx.f10.parse_text(*args, **kwargs)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._service is not None:
                self._service.close()
            self._service = None
            self._closed = True
