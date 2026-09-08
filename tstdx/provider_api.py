# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Direct Provider APIs for provider-specific data.

Unified APIs intentionally cover only common semantics (quotes/bars/...).  This
module exposes each Provider's unique Channels without flattening them into a
large generic ``dict`` API.  Every channel object is bound to one Provider; an
exception never causes a different Provider to be called.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable

from .errors import TdxError, ValidationError
from .providers import PROVIDERS, ProviderSpec, resolve_provider

if TYPE_CHECKING:
    from .service import UnifiedMarketDataService

__all__ = [
    "ProviderAPI",
    "TdxProviderAPI",
    "TencentProviderAPI",
    "SinaProviderAPI",
    "EastmoneyProviderAPI",
    "BaiduProviderAPI",
    "JslProviderAPI",
    "BocProviderAPI",
    "IwencaiProviderAPI",
    "build_provider_api",
]


class ProviderAPI:
    """Base direct namespace bound to exactly one ProviderId."""

    def __init__(self, service: UnifiedMarketDataService, provider: str) -> None:
        self._service = service
        self.provider = resolve_provider(provider=provider)
        self._spec = PROVIDERS.get(self.provider)

    @property
    def spec(self) -> ProviderSpec:
        return self._spec

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

    def _invoke(self, channel: str, fn: Callable[[], Any]) -> Any:
        """Attach canonical Provider/Channel context without changing error type."""
        self._spec.channel(channel)
        try:
            return fn()
        except TdxError as exc:
            exc.context.setdefault("provider", self.provider)
            exc.context.setdefault("channel", channel)
            exc.context.setdefault("fallback", False)
            raise

    def channel(self, channel: str) -> Any:
        """Return the exact adapter/client for one documented Provider Channel."""
        raise ValidationError(
            f"Provider {self.provider!r} 尚未暴露 channel {channel!r} direct adapter",
            context={"provider": self.provider, "channel": channel},
        )


class TdxQuotationAPI:
    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel("quotation")

    def quotes(self, symbols: str | Sequence[str], *, with_meta: bool = False) -> Any:
        return self._owner.quotes(symbols, with_meta=with_meta)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        with_meta: bool = False,
    ) -> Any:
        return self._owner.bars(
            symbol,
            period=period,
            count=count,
            start=start,
            with_meta=with_meta,
        )

    def minute(self, symbol: str) -> Any:
        return self._owner._invoke("quotation", lambda: self.raw.minute_today(symbol))

    def trades(self, symbol: str, *, start: int = 0, count: int = 0) -> Any:
        return self._owner._invoke(
            "quotation",
            lambda: self.raw.trade_today(symbol, start=start, count=count),
        )

    def finance(self, symbol: str) -> Any:
        return self._owner._invoke("quotation", lambda: self.raw.finance_info(symbol))

    def capital_changes(self, symbol: str) -> Any:
        return self._owner._invoke("quotation", lambda: self.raw.capital_changes(symbol))

    def security_count(self, market: str = "sh") -> Any:
        return self._owner._invoke("quotation", lambda: self.raw.security_count(market))


class TdxExtendedAPI:
    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel("extended")

    def markets(self) -> Any:
        return self._owner._invoke("extended", self.raw.ex_market_list)

    def instruments(self, market: int = 0, *, start: int = 0) -> Any:
        return self._owner._invoke(
            "extended", lambda: self.raw.ex_instrument_list(market, start)
        )

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._owner._invoke("extended", lambda: self.raw.ex_quote(symbol, as_format))

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._owner._invoke(
            "extended",
            lambda: self.raw.ex_bars(
                symbol,
                period=period,
                count=count,
                start=start,
                as_format=as_format,
            ),
        )


class TdxGoodsAPI:
    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel("goods")

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._owner._invoke("goods", lambda: self.raw.goods_quote(symbol, as_format))

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._owner._invoke(
            "goods",
            lambda: self.raw.goods_bars(
                symbol,
                period=period,
                count=count,
                start=start,
                as_format=as_format,
            ),
        )

    def instruments(self, market: int = 0, *, start: int = 0) -> Any:
        return self._owner._invoke("goods", lambda: self.raw.goods_list(market, start))


class TdxF10API:
    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel("f10")

    def catalog(self, symbol: str) -> Any:
        return self._owner._invoke("f10", lambda: self.raw.catalog(symbol))

    def download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int = 0,
        length: int = 0,
    ) -> bytes:
        return self._owner._invoke(
            "f10",
            lambda: self.raw.download(symbol, filename, offset=offset, length=length),
        )


class TdxMacAPI:
    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel("mac")

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._owner._invoke("mac", lambda: self.raw.mac_quote(symbol, as_format))

    def blocks(self, block_type: int = 0, *, start: int = 0) -> Any:
        return self._owner._invoke("mac", lambda: self.raw.block_list(block_type, start))

    def block_members(self, block_id: int, *, start: int = 0) -> Any:
        return self._owner._invoke("mac", lambda: self.raw.block_members(block_id, start))


class TdxProviderAPI(ProviderAPI):
    def __init__(self, service: UnifiedMarketDataService) -> None:
        super().__init__(service, "tdx")
        self.quotation = TdxQuotationAPI(self)
        self.extended = TdxExtendedAPI(self)
        self.goods = TdxGoodsAPI(self)
        self.f10 = TdxF10API(self)
        self.mac = TdxMacAPI(self)

    @property
    def raw(self) -> Any:
        return self.quotation.raw

    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid == "vipdoc":
            raise ValidationError(
                "TDX vipdoc 是本地历史 Channel，请使用 reader/vipdoc API；不能替代实时行情",
                context={"provider": "tdx", "channel": "vipdoc"},
            )
        return self._service.manager.tdx_channel(cid)


class WebProviderAPI(ProviderAPI):
    """Exact legacy adapter bridge; never uses WebQuoteSession routing."""

    def _adapter(self, channel: str, factory: Callable[[], Any]) -> Any:
        return self._service.manager.adapter(self.provider, channel, factory)


class TencentProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        timeout = self._service.manager.timeout
        if cid == "quote":
            from .web.adapters import TencentSource

            return self._adapter(cid, lambda: TencentSource(timeout=timeout))
        if cid == "kline":
            from .web.adapters import KlineSource

            return self._adapter(cid, lambda: KlineSource(timeout=timeout))
        if cid == "minute_kline":
            from .web.adapters_ext import MinuteKlineSource

            return self._adapter(cid, lambda: MinuteKlineSource(timeout=timeout))
        if cid == "minute":
            from .web.adapters_ext import MinuteSource

            return self._adapter(cid, lambda: MinuteSource(timeout=timeout))
        if cid == "ticks":
            from .web.ticks import TencentTickSource

            return self._adapter(cid, lambda: TencentTickSource(timeout=timeout))
        if cid == "global":
            from .web.global_market import TencentGlobalSource

            return self._adapter(cid, lambda: TencentGlobalSource(timeout=timeout))
        if cid == "market_stat":
            from .web.global_market import TencentMarketStatSource

            return self._adapter(cid, lambda: TencentMarketStatSource(timeout=timeout))
        if cid == "board_rank":
            from .web.boards import TencentBoardRankSource

            return self._adapter(cid, lambda: TencentBoardRankSource(timeout=timeout))
        return super().channel(cid)

    def minute(self, symbol: str) -> Any:
        return self._invoke("minute", lambda: self.channel("minute").fetch_minute(symbol))

    def ticks(self, symbol: str, *, max_pages: int = 1) -> Any:
        return self._invoke(
            "ticks", lambda: self.channel("ticks").fetch_ticks(symbol, max_pages=max_pages)
        )

    def global_quotes(self, symbols: Sequence[str] = ()) -> Any:
        return self._invoke("global", lambda: self.channel("global").fetch(symbols))

    def market_stat(self, symbols: Sequence[str] = ()) -> Any:
        return self._invoke(
            "market_stat", lambda: self.channel("market_stat").fetch(symbols)
        )

    def board_rank(
        self,
        board: str = "industry",
        *,
        page: int = 1,
        limit: int = 20,
    ) -> Any:
        return self._invoke(
            "board_rank",
            lambda: self.channel("board_rank").fetch_boards(
                board=board,
                page=page,
                limit=limit,
            ),
        )


class SinaProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        timeout = self._service.manager.timeout
        if cid == "quote":
            from .web.adapters import SinaSource

            return self._adapter(cid, lambda: SinaSource(timeout=timeout))
        if cid == "history_kline":
            from .web.history import SinaHistoryKlineSource

            return self._adapter(cid, lambda: SinaHistoryKlineSource(timeout=timeout))
        if cid == "suggest":
            from .web.adapters_ext import SuggestSource

            return self._adapter(cid, lambda: SuggestSource(timeout=timeout))
        if cid == "industry_board":
            from .web.boards import SinaIndustryBoardSource

            return self._adapter(cid, lambda: SinaIndustryBoardSource(timeout=timeout))
        if cid == "board_list":
            from .web.boards import SinaBoardListSource

            return self._adapter(cid, lambda: SinaBoardListSource(timeout=timeout))
        if cid == "board_member":
            from .web.boards import SinaBoardMemberSource

            return self._adapter(cid, lambda: SinaBoardMemberSource(timeout=timeout))
        if cid == "fund_flow":
            from .web.fundflow import SinaFundFlowSource

            return self._adapter(cid, lambda: SinaFundFlowSource(timeout=timeout))
        if cid == "news":
            from .web.news import SinaNewsSource

            return self._adapter(cid, lambda: SinaNewsSource(timeout=timeout))
        return super().channel(cid)

    def suggest(self, key: str, *, limit: int = 10) -> Any:
        return self._invoke(
            "suggest", lambda: self.channel("suggest").fetch_suggest(key, limit=limit)
        )

    def industry_boards(self) -> Any:
        return self._invoke(
            "industry_board", lambda: self.channel("industry_board").fetch_boards()
        )

    def board_list(self, board: str = "concept") -> Any:
        return self._invoke(
            "board_list", lambda: self.channel("board_list").fetch_boards(board)
        )

    def board_members(
        self,
        node: str,
        *,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> Any:
        return self._invoke(
            "board_member",
            lambda: self.channel("board_member").fetch_members(
                node,
                page_size=page_size,
                max_pages=max_pages,
            ),
        )

    def fund_flow(self, symbol: str, *, page: int = 1, size: int = 20) -> Any:
        return self._invoke(
            "fund_flow",
            lambda: self.channel("fund_flow").fetch_stock_flow(symbol, page=page, size=size),
        )

    def news(self, symbol: str, *, page: int = 1, size: int = 20) -> Any:
        return self._invoke(
            "news", lambda: self.channel("news").fetch_news(symbol, page=page, size=size)
        )


class EastmoneyCorporateAPI:
    def __init__(self, owner: EastmoneyProviderAPI) -> None:
        self._owner = owner

    def _adapter(self, key: str, factory: Callable[[], Any]) -> Any:
        # All sub-adapters are semantically inside the documented corporate channel.
        return self._owner._service.manager.adapter("eastmoney", f"corporate:{key}", factory)

    def profile(self, symbol: str) -> Any:
        from .web.corporate import EastmoneyProfileSource

        src = self._adapter("profile", lambda: EastmoneyProfileSource())
        return self._owner._invoke("corporate", lambda: src.fetch_profile(symbol))

    def notices(self, symbols: Sequence[str], *, page: int = 1, size: int = 20) -> Any:
        from .web.corporate import EastmoneyNoticeSource

        src = self._adapter("notices", lambda: EastmoneyNoticeSource())
        return self._owner._invoke(
            "corporate", lambda: src.fetch_notices(symbols, page=page, size=size)
        )

    def reports(self, symbol: str = "", *, page: int = 1, size: int = 20) -> Any:
        from .web.corporate import EastmoneyResearchSource

        src = self._adapter("research", lambda: EastmoneyResearchSource())
        return self._owner._invoke(
            "corporate", lambda: src.fetch_reports(symbol, page=page, size=size)
        )

    def shareholders(self, symbol: str, *, size: int = 10) -> Any:
        from .web.corporate import EastmoneyShareholderSource

        src = self._adapter("shareholders", lambda: EastmoneyShareholderSource())
        return self._owner._invoke(
            "corporate", lambda: src.fetch_free_holders(symbol, size=size)
        )

    def holder_num(self, symbol: str, *, size: int = 10) -> Any:
        from .web.corporate import EastmoneyShareholderSource

        src = self._adapter("shareholders", lambda: EastmoneyShareholderSource())
        return self._owner._invoke(
            "corporate", lambda: src.fetch_holder_num(symbol, size=size)
        )


class EastmoneyProviderAPI(WebProviderAPI):
    def __init__(self, service: UnifiedMarketDataService) -> None:
        super().__init__(service, "eastmoney")
        self.corporate = EastmoneyCorporateAPI(self)

    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        timeout = self._service.manager.timeout
        if cid == "quote":
            from .web.adapters import EastmoneySource

            return self._adapter(cid, lambda: EastmoneySource(timeout=timeout))
        if cid == "kline":
            from .web.history import EastmoneyHistoryKlineSource

            return self._adapter(cid, lambda: EastmoneyHistoryKlineSource(timeout=timeout))
        if cid == "trends":
            from .web.ticks import EastmoneyTrendsSource

            return self._adapter(cid, lambda: EastmoneyTrendsSource(timeout=timeout))
        if cid == "rank":
            from .web.fundflow import EastmoneyRankSource

            return self._adapter(cid, lambda: EastmoneyRankSource(timeout=timeout))
        if cid == "fund_flow":
            from .web.fundflow import EastmoneyFundFlowSource

            return self._adapter(cid, lambda: EastmoneyFundFlowSource(timeout=timeout))
        if cid == "limit_pool":
            from .web.fundflow import EastmoneyLimitPoolSource

            return self._adapter(cid, lambda: EastmoneyLimitPoolSource(timeout=timeout))
        if cid == "stock_changes":
            from .web.fundflow import EastmoneyStockChangesSource

            return self._adapter(cid, lambda: EastmoneyStockChangesSource(timeout=timeout))
        if cid == "northbound":
            from .web.fundflow import EastmoneyNorthboundSource

            return self._adapter(cid, lambda: EastmoneyNorthboundSource(timeout=timeout))
        if cid == "hot_rank":
            from .web.hot_rank import EastmoneyHotRankSource

            return self._adapter(cid, lambda: EastmoneyHotRankSource(timeout=timeout))
        if cid == "longhu":
            from .web.longhu import EastmoneyTopListSource

            return self._adapter(cid, lambda: EastmoneyTopListSource(timeout=timeout))
        if cid == "margin":
            from .web.adapters_margin import EastmoneyMarginSource

            return self._adapter(cid, lambda: EastmoneyMarginSource(timeout=timeout))
        if cid == "index_constituents":
            from .web.adapters_index import EastmoneyIndexConstituentsSource

            return self._adapter(cid, lambda: EastmoneyIndexConstituentsSource(timeout=timeout))
        if cid == "fund":
            from .web.adapters_fund import FundSource

            return self._adapter(cid, lambda: FundSource(timeout=timeout))
        if cid == "corporate":
            return self.corporate
        return super().channel(cid)

    def trends(self, symbol: str, *, ndays: int = 1) -> Any:
        return self._invoke(
            "trends", lambda: self.channel("trends").fetch_minutes(symbol, ndays=ndays)
        )

    def rank(
        self,
        market: str = "all_a",
        *,
        sort: str = "change_pct",
        limit: int = 20,
        page: int = 1,
        ascending: bool = False,
    ) -> Any:
        return self._invoke(
            "rank",
            lambda: self.channel("rank").fetch_rows(
                market,
                sort=sort,
                limit=limit,
                page=page,
                ascending=ascending,
            ),
        )

    def fund_flow(self, symbols: Sequence[str]) -> Any:
        return self._invoke(
            "fund_flow", lambda: self.channel("fund_flow").fetch_flow(symbols)
        )

    def fund_flow_history(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 10,
    ) -> Any:
        return self._invoke(
            "fund_flow",
            lambda: self.channel("fund_flow").fetch_history(
                symbol,
                period=period,
                count=count,
            ),
        )

    def limit_pool(
        self,
        pool: str = "zt",
        *,
        date: str = "",
        page: int = 1,
        limit: int = 20,
    ) -> Any:
        return self._invoke(
            "limit_pool",
            lambda: self.channel("limit_pool").fetch_pool(
                pool,
                date=date,
                page=page,
                limit=limit,
            ),
        )

    def stock_changes(
        self,
        types: Sequence[int] = (),
        *,
        page: int = 1,
        size: int = 50,
    ) -> Any:
        return self._invoke(
            "stock_changes",
            lambda: self.channel("stock_changes").fetch_changes(
                tuple(types),
                page=page,
                size=size,
            ),
        )

    def northbound(self) -> Any:
        return self._invoke(
            "northbound", lambda: self.channel("northbound").fetch_northbound()
        )

    def hot_rank(self, *, page: int = 1, size: int = 100) -> Any:
        return self._invoke(
            "hot_rank",
            lambda: self.channel("hot_rank").fetch_hot_rank(page=page, size=size),
        )


class BaiduProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid not in {"quote", "kline", "minute", "ticks"}:
            return super().channel(cid)
        from .web.adapters_baidu import BaiduSource

        return self._adapter(cid, lambda: BaiduSource(timeout=self._service.manager.timeout))

    def minute(self, symbol: str) -> Any:
        return self._invoke("minute", lambda: self.channel("minute").fetch_minute(symbol))

    def ticks(self, symbol: str, *, limit: int = 200) -> Any:
        return self._invoke(
            "ticks", lambda: self.channel("ticks").fetch_ticks(symbol, limit=limit)
        )


class JslProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid not in {"bond", "etf"}:
            return super().channel(cid)
        from .web.adapters import JslSource

        return self._adapter(cid, lambda: JslSource(timeout=self._service.manager.timeout))

    def bonds(self) -> Any:
        return self._invoke("bond", lambda: self.channel("bond").fetch([]))


class BocProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid != "fx":
            return super().channel(cid)
        from .web.adapters import BocSource

        return self._adapter(cid, lambda: BocSource(timeout=self._service.manager.timeout))

    def fx_rates(self) -> Any:
        return self._invoke("fx", lambda: self.channel("fx").fetch_rates())


class IwencaiProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid != "screening":
            return super().channel(cid)
        from .web.wencai import WencaiSource

        return self._adapter(cid, lambda: WencaiSource(timeout=self._service.manager.timeout))

    def screen(self, query: str, *, page: int = 1, limit: int = 50) -> Any:
        return self._invoke(
            "screening",
            lambda: self.channel("screening").fetch_strategy(
                query,
                page=page,
                limit=limit,
            ),
        )

    query = screen


def build_provider_api(service: UnifiedMarketDataService, provider: str) -> ProviderAPI:
    pid = resolve_provider(provider=provider)
    factories: dict[str, type[ProviderAPI]] = {
        "tdx": TdxProviderAPI,
        "tencent": TencentProviderAPI,
        "sina": SinaProviderAPI,
        "eastmoney": EastmoneyProviderAPI,
        "baidu": BaiduProviderAPI,
        "jsl": JslProviderAPI,
        "boc": BocProviderAPI,
        "iwencai": IwencaiProviderAPI,
    }
    try:
        cls = factories[pid]
    except KeyError as exc:
        raise ValidationError(
            f"Provider {pid!r} 尚无 Direct API",
            context={"provider": pid},
        ) from exc
    return cls(service)  # type: ignore[call-arg]
