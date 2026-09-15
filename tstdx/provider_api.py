# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Direct Provider/Channel APIs.

Unified APIs cover only common semantics. Provider-specific data stays under
``md.<provider>``. Web Provider namespaces are table-driven: one canonical
ProviderId owns a documented set of Channel -> Adapter bindings, all sharing the
ProviderManager lifecycle. No path in this module performs Provider fallback.

The Direct API contract is validated from the canonical Provider Registry at
module import: every registered Provider has exactly one API type, every
non-local registered Channel has an explicit Direct mapping, and unsupported
unified capabilities fail before the service performs I/O.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from .errors import InternalError, TdxError, ValidationError
from .providers import PROVIDERS, ProviderSpec, resolve_provider

if TYPE_CHECKING:
    from .service import UnifiedMarketDataService

__all__ = [
    "ProviderAPI",
    "TdxProviderAPI",
    "LocalVipdocProviderAPI",
    "TencentProviderAPI",
    "SinaProviderAPI",
    "EastmoneyProviderAPI",
    "BaiduProviderAPI",
    "JslProviderAPI",
    "BocProviderAPI",
    "IwencaiProviderAPI",
    "CompositeProviderAPI",
    "DerivedProviderAPI",
    "BuiltinProviderAPI",
    "build_provider_api",
]

AdapterRef = tuple[str, str]


class ProviderAPI:
    """Base namespace bound to exactly one canonical ProviderId."""

    provider_id: ClassVar[str | None] = None

    #: ``True`` for composite Providers (``derived`` / ``builtin``) that expose
    #: capabilities only through the unified QuerySpec path. They own no Direct
    #: channel API, so the registry ↔ Direct-channel parity check is skipped for
    #: them instead of forcing a fake adapter per channel.
    CHANNEL_API_EXEMPT: ClassVar[bool] = False

    def __init__(
        self,
        service: UnifiedMarketDataService,
        provider: str | None = None,
    ) -> None:
        selected = provider or self.provider_id
        if selected is None:
            raise TypeError("ProviderAPI requires provider or provider_id")
        self._service = service
        self.provider = resolve_provider(provider=selected)
        self._spec = PROVIDERS.get(self.provider)

    @property
    def spec(self) -> ProviderSpec:
        return self._spec

    def _require_capability(self, capability: str) -> None:
        """Fail before I/O when a Provider does not own a unified capability."""

        if self._spec.supports(capability):
            return
        raise ValidationError(
            f"Provider {self.provider!r} 不支持 Direct capability {capability!r}",
            context={
                "provider": self.provider,
                "capability": capability,
                "phase": "direct_contract",
                "fallback": False,
                "provider_switch_allowed": False,
            },
        )

    def quotes(self, symbols: str | Sequence[str], *, with_meta: bool = False) -> Any:
        self._require_capability("quotes")
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
        self._require_capability("bars")
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
            exc.context.setdefault("provider_switch_allowed", False)
            raise
        except Exception as exc:
            raise InternalError(
                "Direct Provider adapter 未处理异常",
                context={
                    "provider": self.provider,
                    "channel": channel,
                    "fallback": False,
                    "provider_switch_allowed": False,
                    "cause_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

    def channel(self, channel: str) -> Any:
        raise ValidationError(
            f"Provider {self.provider!r} 尚未暴露 channel {channel!r} Direct API",
            context={
                "provider": self.provider,
                "channel": channel,
                "phase": "direct_contract",
                "fallback": False,
                "provider_switch_allowed": False,
            },
        )


class _TdxChannel:
    channel_id: ClassVar[str] = ""

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
    provider_id = "tdx"
    DIRECT_CHANNELS: ClassVar[frozenset[str]] = frozenset(
        {"quotation", "extended", "goods", "f10", "mac"}
    )

    def __init__(self, service: UnifiedMarketDataService) -> None:
        super().__init__(service)
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
                context={
                    "provider": "tdx",
                    "channel": "vipdoc",
                    "phase": "direct_contract",
                    "fallback": False,
                    "provider_switch_allowed": False,
                },
            )
        return self._service.manager.tdx_channel(cid)


class WebProviderAPI(ProviderAPI):
    """Table-driven Web Provider namespace."""

    CHANNELS: ClassVar[dict[str, AdapterRef]] = {}
    EXPLICIT_CHANNELS: ClassVar[frozenset[str]] = frozenset()

    @staticmethod
    def _load_adapter(ref: AdapterRef) -> type[Any]:
        module_name, class_name = ref
        module = importlib.import_module(module_name)
        return getattr(module, class_name)

    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        self.spec.channel(cid)
        try:
            ref = self.CHANNELS[cid]
        except KeyError as exc:
            raise ValidationError(
                f"Provider {self.provider!r} 的 channel {cid!r} 尚无 Direct Adapter",
                context={
                    "provider": self.provider,
                    "channel": cid,
                    "phase": "direct_contract",
                    "fallback": False,
                    "provider_switch_allowed": False,
                },
            ) from exc
        return self._service.manager.web_adapter(
            self.provider,
            cid,
            self._load_adapter(ref),
        )


class LocalVipdocProviderAPI(WebProviderAPI):
    """Local TDX vipdoc namespace.

    ``local_vipdoc`` 是显式的本地历史数据 Provider：其唯一 channel ``vipdoc``
    标记为 ``local=True``，属于本地文件执行器而非在线 Direct 通道，因此本类型的
    Direct channel 表为空。读取本地 vipdoc 必须经 ``bars`` 能力走本地执行器，
    绝不允许以 Direct API 之名冒充在线 TDX 行情。
    """

    provider_id = "local_vipdoc"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {}
    EXPLICIT_CHANNELS: ClassVar[frozenset[str]] = frozenset()


class TencentProviderAPI(WebProviderAPI):
    provider_id = "tencent"
    CHANNELS = {
        "quote": ("tstdx.web.adapters", "TencentSource"),
        "kline": ("tstdx.web.adapters", "KlineSource"),
        "minute_kline": ("tstdx.web.adapters_ext", "MinuteKlineSource"),
        "minute": ("tstdx.web.adapters_ext", "MinuteSource"),
        "ticks": ("tstdx.web.ticks", "TencentTickSource"),
        "global": ("tstdx.web.global_market", "TencentGlobalSource"),
        "market_stat": ("tstdx.web.global_market", "TencentMarketStatSource"),
        "board_rank": ("tstdx.web.boards", "TencentBoardRankSource"),
        "catalog": ("tstdx.web.facade", "WebQuoteSession"),
    }

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
    provider_id = "sina"
    CHANNELS = {
        "quote": ("tstdx.web.adapters", "SinaSource"),
        "history_kline": ("tstdx.web.history", "SinaHistoryKlineSource"),
        "suggest": ("tstdx.web.adapters_ext", "SuggestSource"),
        "industry_board": ("tstdx.web.boards", "SinaIndustryBoardSource"),
        "board_list": ("tstdx.web.boards", "SinaBoardListSource"),
        "board_member": ("tstdx.web.boards", "SinaBoardMemberSource"),
        "fund_flow": ("tstdx.web.fundflow", "SinaFundFlowSource"),
        "news": ("tstdx.web.news", "SinaNewsSource"),
        "catalog": ("tstdx.web.facade", "WebQuoteSession"),
    }

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
    """Corporate sub-resources sharing one Eastmoney Provider HTTP client."""

    ADAPTERS: ClassVar[dict[str, AdapterRef]] = {
        "profile": ("tstdx.web.corporate", "EastmoneyProfileSource"),
        "notices": ("tstdx.web.corporate", "EastmoneyNoticeSource"),
        "research": ("tstdx.web.corporate", "EastmoneyResearchSource"),
        "shareholders": ("tstdx.web.corporate", "EastmoneyShareholderSource"),
        "block_trades": ("tstdx.web.corporate", "EastmoneyBlockTradeSource"),
        "unlocks": ("tstdx.web.corporate", "EastmoneyUnlockSource"),
        "performance": ("tstdx.web.corporate", "EastmoneyPerformanceSource"),
    }

    def __init__(self, owner: EastmoneyProviderAPI) -> None:
        self._owner = owner

    def _adapter(self, key: str) -> Any:
        try:
            ref = self.ADAPTERS[key]
        except KeyError as exc:
            raise ValidationError(f"未知 Eastmoney corporate resource {key!r}") from exc
        cls = WebProviderAPI._load_adapter(ref)
        return self._owner._service.manager.web_adapter(
            "eastmoney",
            "corporate",
            cls,
            resource_key=f"corporate:{key}",
        )

    def profile(self, symbol: str) -> Any:
        src = self._adapter("profile")
        return self._owner._invoke("corporate", lambda: src.fetch_profile(symbol))

    def notices(self, symbols: Sequence[str], *, page: int = 1, size: int = 20) -> Any:
        src = self._adapter("notices")
        return self._owner._invoke(
            "corporate", lambda: src.fetch_notices(list(symbols), page=page, size=size)
        )

    def reports(self, symbol: str = "", *, page: int = 1, size: int = 20) -> Any:
        src = self._adapter("research")
        return self._owner._invoke(
            "corporate", lambda: src.fetch_reports(symbol, page=page, size=size)
        )

    def shareholders(self, symbol: str, *, size: int = 20) -> Any:
        src = self._adapter("shareholders")
        return self._owner._invoke(
            "corporate", lambda: src.fetch_free_holders(symbol, size=size)
        )

    def holder_num(self, symbol: str, *, size: int = 20) -> Any:
        src = self._adapter("shareholders")
        return self._owner._invoke(
            "corporate", lambda: src.fetch_holder_num(symbol, size=size)
        )

    def block_trades(self, symbol: str = "", *, date: str = "", size: int = 20) -> Any:
        src = self._adapter("block_trades")
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
        src = self._adapter("unlocks")
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
        src = self._adapter("performance")
        return self._owner._invoke(
            "corporate",
            lambda: src.fetch_performance(
                symbol=symbol,
                report_date=report_date,
                size=size,
            ),
        )


class EastmoneyProviderAPI(WebProviderAPI):
    provider_id = "eastmoney"
    EXPLICIT_CHANNELS = frozenset({"corporate"})
    CHANNELS = {
        "quote": ("tstdx.web.adapters", "EastmoneySource"),
        "kline": ("tstdx.web.history", "EastmoneyHistoryKlineSource"),
        "trends": ("tstdx.web.ticks", "EastmoneyTrendsSource"),
        "rank": ("tstdx.web.fundflow", "EastmoneyRankSource"),
        "fund_flow": ("tstdx.web.fundflow", "EastmoneyFundFlowSource"),
        "limit_pool": ("tstdx.web.fundflow", "EastmoneyLimitPoolSource"),
        "stock_changes": ("tstdx.web.fundflow", "EastmoneyStockChangesSource"),
        "northbound": ("tstdx.web.fundflow", "EastmoneyNorthboundSource"),
        "hot_rank": ("tstdx.web.hot_rank", "EastmoneyHotRankSource"),
        "longhu": ("tstdx.web.longhu", "EastmoneyTopListSource"),
        "margin": ("tstdx.web.adapters_margin", "EastmoneyMarginSource"),
        "index_constituents": (
            "tstdx.web.adapters_index",
            "EastmoneyIndexConstituentsSource",
        ),
        "fund": ("tstdx.web.adapters_fund", "FundSource"),
        "derivatives": ("tstdx.web.efinance_deriv", "EastmoneyFuturesSource"),
        "datacenter": ("tstdx.web.fin_report", "EastmoneyF10ReportSource"),
        "news": ("tstdx.web.news", "EastmoneyNewsSource"),
        "research": ("tstdx.web.news", "EastmoneyResearchVisitSource"),
        "options": ("tstdx.web.efinance_options", "EastmoneyOptionsSource"),
        "catalog": ("tstdx.web.facade", "WebQuoteSession"),
    }

    def __init__(self, service: UnifiedMarketDataService) -> None:
        super().__init__(service)
        self.corporate = EastmoneyCorporateAPI(self)

    def channel(self, channel: str) -> Any:
        cid = str(channel).strip().lower()
        if cid == "corporate":
            self.spec.channel(cid)
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

    def futures_base_info(self) -> Any:
        return self._invoke(
            "derivatives", lambda: self.channel("derivatives").fetch_base_info()
        )

    def report(
        self,
        symbol: str,
        *,
        report: str = "balance_sheet",
        report_date: str = "",
        page: int = 1,
        size: int = 10,
    ) -> Any:
        return self._invoke(
            "datacenter",
            lambda: self.channel("datacenter").fetch_report(
                symbol,
                report=report,
                report_date=report_date,
                page=page,
                size=size,
            ),
        )

    def news(self, *, page: int = 1, size: int = 30) -> Any:
        return self._invoke(
            "news", lambda: self.channel("news").fetch_news(page=page, size=size)
        )

    def research_visits(self, symbol: str, *, page: int = 1, size: int = 20) -> Any:
        return self._invoke(
            "research",
            lambda: self.channel("research").fetch_visits(symbol, page=page, size=size),
        )

    def options_contracts(
        self,
        *,
        market: str = "",
        page: int = 1,
        size: int = 200,
    ) -> Any:
        return self._invoke(
            "options",
            lambda: self.channel("options").fetch_contract_list(
                market=market,
                page=page,
                size=size,
            ),
        )


class BaiduProviderAPI(WebProviderAPI):
    provider_id = "baidu"
    CHANNELS = {
        "quote": ("tstdx.web.adapters_baidu", "BaiduSource"),
        "kline": ("tstdx.web.adapters_baidu", "BaiduSource"),
        "minute": ("tstdx.web.adapters_baidu", "BaiduSource"),
        "ticks": ("tstdx.web.adapters_baidu", "BaiduSource"),
        "catalog": ("tstdx.web.facade", "WebQuoteSession"),
    }

    def minute(self, symbol: str) -> Any:
        return self._invoke("minute", lambda: self.channel("minute").fetch_minute(symbol))

    def ticks(self, symbol: str, *, limit: int = 200) -> Any:
        return self._invoke(
            "ticks", lambda: self.channel("ticks").fetch_ticks(symbol, limit=limit)
        )


class JslProviderAPI(WebProviderAPI):
    provider_id = "jsl"
    CHANNELS = {
        "bond": ("tstdx.web.adapters", "JslSource"),
    }

    def bonds(self) -> Any:
        return self._invoke("bond", lambda: self.channel("bond").fetch([]))


class BocProviderAPI(WebProviderAPI):
    provider_id = "boc"
    CHANNELS = {"fx": ("tstdx.web.adapters", "BocSource")}

    def fx_rates(self) -> Any:
        return self._invoke("fx", lambda: self.channel("fx").fetch_rates())


class IwencaiProviderAPI(WebProviderAPI):
    provider_id = "iwencai"
    CHANNELS = {"screening": ("tstdx.web.wencai", "WencaiSource")}

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


class CompositeProviderAPI(ProviderAPI):
    """Composite Provider namespace with no Direct channel API.

    ``derived`` (honest aggregates over first-party Providers) and ``builtin``
    (static built-in catalogs) are canonical registry Providers so that the
    migrated-capability catalog has stable homes, but they are reached only
    through the unified :class:`~tstdx.query.QuerySpec` path. They deliberately
    expose no Direct channel API rather than a fake adapter, so the registry ↔
    Direct-channel parity check is skipped for this hierarchy.
    """

    CHANNEL_API_EXEMPT: ClassVar[bool] = True
    DIRECT_CHANNELS: ClassVar[frozenset[str]] = frozenset()


class DerivedProviderAPI(CompositeProviderAPI):
    provider_id = "derived"


class BuiltinProviderAPI(CompositeProviderAPI):
    provider_id = "builtin"


def _build_provider_api_types(
    api_types: Sequence[type[ProviderAPI]],
) -> dict[str, type[ProviderAPI]]:
    """Build and validate the Direct API type/channel contract from Registry."""

    mapping: dict[str, type[ProviderAPI]] = {}
    for api_type in api_types:
        provider_id = api_type.provider_id
        if provider_id is None:
            raise RuntimeError(f"Direct API type {api_type.__name__} has no provider_id")
        pid = resolve_provider(provider=provider_id)
        if pid in mapping:
            raise RuntimeError(f"duplicate Direct API type for Provider {pid!r}")
        mapping[pid] = api_type

    registered_ids = set(PROVIDERS.ids())
    if set(mapping) != registered_ids:
        missing = sorted(registered_ids - set(mapping))
        extra = sorted(set(mapping) - registered_ids)
        raise RuntimeError(
            "Direct Provider API registry mismatch: "
            f"missing API types for {missing}, unknown Providers {extra}"
        )

    for pid, api_type in mapping.items():
        if api_type.CHANNEL_API_EXEMPT:
            # Composite Providers intentionally expose no Direct channel API.
            continue
        spec = PROVIDERS.get(pid)
        expected_channels = {channel.id for channel in spec.channels if not channel.local}
        if issubclass(api_type, WebProviderAPI):
            mapped_channels = set(api_type.CHANNELS) | set(api_type.EXPLICIT_CHANNELS)
        elif api_type is TdxProviderAPI:
            mapped_channels = set(TdxProviderAPI.DIRECT_CHANNELS)
        else:
            mapped_channels = set()
        if mapped_channels != expected_channels:
            missing = sorted(expected_channels - mapped_channels)
            extra = sorted(mapped_channels - expected_channels)
            raise RuntimeError(
                f"Direct channel contract mismatch for Provider {pid!r}: "
                f"missing={missing} undeclared={extra}"
            )
    return mapping


_PROVIDER_API_TYPES: dict[str, type[ProviderAPI]] = _build_provider_api_types(
    (
        TdxProviderAPI,
        LocalVipdocProviderAPI,
        TencentProviderAPI,
        SinaProviderAPI,
        EastmoneyProviderAPI,
        BaiduProviderAPI,
        JslProviderAPI,
        BocProviderAPI,
        IwencaiProviderAPI,
        DerivedProviderAPI,
        BuiltinProviderAPI,
    )
)


def build_provider_api(service: UnifiedMarketDataService, provider: str) -> ProviderAPI:
    pid = resolve_provider(provider=provider)
    try:
        cls = _PROVIDER_API_TYPES[pid]
    except KeyError as exc:
        raise ValidationError(
            f"Provider {pid!r} 尚无 Direct API",
            context={
                "provider": pid,
                "phase": "direct_contract",
                "fallback": False,
                "provider_switch_allowed": False,
            },
        ) from exc
    return cls(service)
