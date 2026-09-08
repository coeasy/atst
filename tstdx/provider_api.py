# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Direct Provider/Channel APIs.

Unified APIs cover only genuinely common semantics. Provider-specific data stays
under ``md.<provider>`` and every adapter is bound to exactly one Provider. All
Web Channels of the same Provider reuse that Provider's HTTP client; failures
are annotated with Provider/Channel provenance and never trigger another
Provider.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

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
    """Base namespace bound to one canonical ProviderId."""

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

    def _invoke(self, channel: str, fn: Any) -> Any:
        self._spec.channel(channel)
        try:
            return fn()
        except TdxError as exc:
            exc.context.setdefault("provider", self.provider)
            exc.context.setdefault("channel", channel)
            exc.context.setdefault("fallback", False)
            raise

    def channel(self, channel: str) -> Any:
        raise ValidationError(
            f"Provider {self.provider!r} 尚未暴露 channel {channel!r} Direct API",
            context={"provider": self.provider, "channel": channel},
        )


class _TdxChannel:
    channel_id = ""

    def __init__(self, owner: TdxProviderAPI) -> None:
        self._owner = owner

    @property
    def raw(self) -> Any:
        return self._owner._service.manager.tdx_channel(self.channel_id)

    def _call(self, fn: Any) -> Any:
        return self._owner._invoke(self.channel_id, fn)


class TdxQuotationAPI(_TdxChannel):
    channel_id = "quotation"

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
        return self._call(lambda: self.raw.minute_today(symbol))

    def trades(self, symbol: str, *, start: int = 0, count: int = 0) -> Any:
        return self._call(lambda: self.raw.trade_today(symbol, start=start, count=count))

    def finance(self, symbol: str) -> Any:
        return self._call(lambda: self.raw.finance_info(symbol))

    def capital_changes(self, symbol: str) -> Any:
        return self._call(lambda: self.raw.capital_changes(symbol))

    def security_count(self, market: str = "sh") -> Any:
        return self._call(lambda: self.raw.security_count(market))


class TdxExtendedAPI(_TdxChannel):
    channel_id = "extended"

    def markets(self) -> Any:
        return self._call(self.raw.ex_market_list)

    def instruments(self, market: int = 0, *, start: int = 0) -> Any:
        return self._call(lambda: self.raw.ex_instrument_list(market, start))

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._call(lambda: self.raw.ex_quote(symbol, as_format))

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._call(
            lambda: self.raw.ex_bars(
                symbol,
                period=period,
                count=count,
                start=start,
                as_format=as_format,
            )
        )


class TdxGoodsAPI(_TdxChannel):
    channel_id = "goods"

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._call(lambda: self.raw.goods_quote(symbol, as_format))

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> Any:
        return self._call(
            lambda: self.raw.goods_bars(
                symbol,
                period=period,
                count=count,
                start=start,
                as_format=as_format,
            )
        )

    def instruments(self, market: int = 0, *, start: int = 0) -> Any:
        return self._call(lambda: self.raw.goods_list(market, start))


class TdxF10API(_TdxChannel):
    channel_id = "f10"

    def catalog(self, symbol: str) -> Any:
        return self._call(lambda: self.raw.catalog(symbol))

    def download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int = 0,
        length: int = 0,
    ) -> bytes:
        return self._call(
            lambda: self.raw.download(symbol, filename, offset=offset, length=length)
        )


class TdxMacAPI(_TdxChannel):
    channel_id = "mac"

    def quote(self, symbol: str, *, as_format: str = "dict") -> Any:
        return self._call(lambda: self.raw.mac_quote(symbol, as_format))

    def blocks(self, block_type: int = 0, *, start: int = 0) -> Any:
        return self._call(lambda: self.raw.block_list(block_type, start))

    def block_members(self, block_id: int, *, start: int = 0) -> Any:
        return self._call(lambda: self.raw.block_members(block_id, start))


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
                "TDX vipdoc 是 local_historical Channel；不能替代在线 TDX 行情",
                context={"provider": "tdx", "channel": "vipdoc"},
            )
        return self._service.manager.tdx_channel(cid)


class WebProviderAPI(ProviderAPI):
    """Base for exact Web Provider namespaces."""

    def _web(
        self,
        channel: str,
        adapter_cls: type[Any],
        *,
        resource_key: str | None = None,
    ) -> Any:
        return self._service.manager.web_adapter(
            self.provider,
            channel,
            adapter_cls,
            resource_key=resource_key,
        )


class TencentProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid == "quote":
            from .web.adapters import TencentSource

            return self._web(cid, TencentSource)
        if cid == "kline":
            from .web.adapters import KlineSource

            return self._web(cid, KlineSource)
        if cid == "minute_kline":
            from .web.adapters_ext import MinuteKlineSource

            return self._web(cid, MinuteKlineSource)
        if cid == "minute":
            from .web.adapters_ext import MinuteSource

            return self._web(cid, MinuteSource)
        if cid == "ticks":
            from .web.ticks import TencentTickSource

            return self._web(cid, TencentTickSource)
        if cid == "global":
            from .web.global_market import TencentGlobalSource

            return self._web(cid, TencentGlobalSource)
        if cid == "market_stat":
            from .web.global_market import TencentMarketStatSource

            return self._web(cid, TencentMarketStatSource)
        if cid == "board_rank":
            from .web.boards import TencentBoardRankSource

            return self._web(cid, TencentBoardRankSource)
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
        return self._invoke("market_stat", lambda: self.channel("market_stat").fetch(symbols))

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
                board,
                limit=limit,
                page=page,
            ),
        )


class SinaProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid == "quote":
            from .web.adapters import SinaSource

            return self._web(cid, SinaSource)
        if cid == "history_kline":
            from .web.history import SinaHistoryKlineSource

            return self._web(cid, SinaHistoryKlineSource)
        if cid == "suggest":
            from .web.adapters_ext import SuggestSource

            return self._web(cid, SuggestSource)
        if cid == "industry_board":
            from .web.boards import SinaIndustryBoardSource

            return self._web(cid, SinaIndustryBoardSource)
        if cid == "board_list":
            from .web.boards import SinaBoardListSource

            return self._web(cid, SinaBoardListSource)
        if cid == "board_member":
            from .web.boards import SinaBoardMemberSource

            return self._web(cid, SinaBoardMemberSource)
        if cid == "fund_flow":
            from .web.fundflow import SinaFundFlowSource

            return self._web(cid, SinaFundFlowSource)
        if cid == "news":
            from .web.news import SinaNewsSource

            return self._web(cid, SinaNewsSource)
        return super().channel(cid)

    def suggest(self, key: str, *, limit: int = 10) -> Any:
        return self._invoke(
            "suggest",
            lambda: self.channel("suggest").fetch_suggest(key, limit=limit),
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

    def news(
        self,
        symbol: str,
        *,
        page: int = 1,
        num: int = 20,
        tag: str | None = None,
    ) -> Any:
        return self._invoke(
            "news",
            lambda: self.channel("news").fetch_news(symbol, page=page, num=num, tag=tag),
        )


class EastmoneyCorporateAPI:
    """Provider-specific corporate APIs sharing the Eastmoney HTTP client."""

    def __init__(self, owner: EastmoneyProviderAPI) -> None:
        self._owner = owner

    def _web(self, key: str, adapter_cls: type[Any]) -> Any:
        return self._owner._service.manager.web_adapter(
            "eastmoney",
            "corporate",
            adapter_cls,
            resource_key=f"corporate:{key}",
        )

    def profile(self, symbol: str) -> Any:
        from .web.corporate import EastmoneyProfileSource

        src = self._web("profile", EastmoneyProfileSource)
        return self._owner._invoke("corporate", lambda: src.fetch_profile(symbol))

    def notices(self, symbols: Sequence[str], *, page: int = 1, size: int = 20) -> Any:
        from .web.corporate import EastmoneyNoticeSource

        src = self._web("notices", EastmoneyNoticeSource)
        return self._owner._invoke(
            "corporate", lambda: src.fetch_notices(list(symbols), page=page, size=size)
        )

    def reports(self, symbol: str = "", *, page: int = 1, size: int = 20) -> Any:
        from .web.corporate import EastmoneyResearchSource

        src = self._web("research", EastmoneyResearchSource)
        return self._owner._invoke(
            "corporate", lambda: src.fetch_reports(symbol, page=page, size=size)
        )

    def shareholders(self, symbol: str, *, size: int = 10) -> Any:
        from .web.corporate import EastmoneyShareholderSource

        src = self._web("shareholders", EastmoneyShareholderSource)
        return self._owner._invoke(
            "corporate", lambda: src.fetch_free_holders(symbol, size=size)
        )

    def holder_num(self, symbol: str, *, size: int = 10) -> Any:
        from .web.corporate import EastmoneyShareholderSource

        src = self._web("shareholders", EastmoneyShareholderSource)
        return self._owner._invoke(
            "corporate", lambda: src.fetch_holder_num(symbol, size=size)
        )

    def block_trades(self, symbol: str = "", *, date: str = "", size: int = 20) -> Any:
        from .web.corporate import EastmoneyBlockTradeSource

        src = self._web("block_trades", EastmoneyBlockTradeSource)
        return self._owner._invoke(
            "corporate",
            lambda: src.fetch_block_trades(symbol=symbol, date=date, size=size),
        )

    def unlocks(
        self,
        symbol: str = "",
        *,
        begin: str = "",
        end: str = "",
        size: int = 20,
    ) -> Any:
        from .web.corporate import EastmoneyUnlockSource

        src = self._web("unlocks", EastmoneyUnlockSource)
        return self._owner._invoke(
            "corporate",
            lambda: src.fetch_unlocks(symbol=symbol, begin=begin, end=end, size=size),
        )

    def performance(
        self,
        symbol: str = "",
        *,
        report_date: str = "",
        size: int = 20,
    ) -> Any:
        from .web.corporate import EastmoneyPerformanceSource

        src = self._web("performance", EastmoneyPerformanceSource)
        return self._owner._invoke(
            "corporate",
            lambda: src.fetch_performance(
                symbol=symbol,
                report_date=report_date,
                size=size,
            ),
        )


class EastmoneyProviderAPI(WebProviderAPI):
    def __init__(self, service: UnifiedMarketDataService) -> None:
        super().__init__(service, "eastmoney")
        self.corporate = EastmoneyCorporateAPI(self)

    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid == "quote":
            from .web.adapters import EastmoneySource

            return self._web(cid, EastmoneySource)
        if cid == "kline":
            from .web.history import EastmoneyHistoryKlineSource

            return self._web(cid, EastmoneyHistoryKlineSource)
        if cid == "trends":
            from .web.ticks import EastmoneyTrendsSource

            return self._web(cid, EastmoneyTrendsSource)
        if cid == "rank":
            from .web.fundflow import EastmoneyRankSource

            return self._web(cid, EastmoneyRankSource)
        if cid == "fund_flow":
            from .web.fundflow import EastmoneyFundFlowSource

            return self._web(cid, EastmoneyFundFlowSource)
        if cid == "limit_pool":
            from .web.fundflow import EastmoneyLimitPoolSource

            return self._web(cid, EastmoneyLimitPoolSource)
        if cid == "stock_changes":
            from .web.fundflow import EastmoneyStockChangesSource

            return self._web(cid, EastmoneyStockChangesSource)
        if cid == "northbound":
            from .web.fundflow import EastmoneyNorthboundSource

            return self._web(cid, EastmoneyNorthboundSource)
        if cid == "hot_rank":
            from .web.hot_rank import EastmoneyHotRankSource

            return self._web(cid, EastmoneyHotRankSource)
        if cid == "longhu":
            from .web.longhu import EastmoneyTopListSource

            return self._web(cid, EastmoneyTopListSource)
        if cid == "margin":
            from .web.adapters_margin import EastmoneyMarginSource

            return self._web(cid, EastmoneyMarginSource)
        if cid == "index_constituents":
            from .web.adapters_index import EastmoneyIndexConstituentsSource

            return self._web(cid, EastmoneyIndexConstituentsSource)
        if cid == "fund":
            from .web.adapters_fund import FundSource

            return self._web(cid, FundSource)
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
            "hot_rank", lambda: self.channel("hot_rank").fetch_hot_rank(page=page, size=size)
        )


class BaiduProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid not in {"quote", "kline", "minute", "ticks"}:
            return super().channel(cid)
        from .web.adapters_baidu import BaiduSource

        return self._web(cid, BaiduSource)

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

        return self._web(cid, JslSource)

    def bonds(self) -> Any:
        return self._invoke("bond", lambda: self.channel("bond").fetch([]))


class BocProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid != "fx":
            return super().channel(cid)
        from .web.adapters import BocSource

        return self._web(cid, BocSource)

    def fx_rates(self) -> Any:
        return self._invoke("fx", lambda: self.channel("fx").fetch_rates())


class IwencaiProviderAPI(WebProviderAPI):
    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid != "screening":
            return super().channel(cid)
        from .web.wencai import WencaiSource

        return self._web(cid, WencaiSource)

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
    return cls(service)
