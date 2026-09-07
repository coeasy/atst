# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（百度财经 + 基金 / 指数成分 / 联想搜索 / 问财）。

本模块只承载 :class:`tstdx.web.facade.WebQuoteSession` 的方法**纯搬移**
（方法体逐字不变）：:class:`BaiduSessionMixin`。
组合与 ``__init__`` / ``close`` 见 :mod:`tstdx.web.facade`。
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Bar, MinutePoint, Quote, Tick
from ._facade_mixin_market import INDEX_SYMBOLS, _shared_http  # noqa: F401  共享助手

__all__ = ["BaiduSessionMixin"]


class BaiduSessionMixin:
    """百度财经系列 + 基金 / 指数成分 / 联想搜索 / 问财。"""

    # -- 百度财经源（B0） ---------------------------------------------------- #
    @staticmethod
    def baidu_kline(
        symbol: str, *, period: str = "day", count: int = 320, end_time: int | None = None
    ) -> list[Bar]:
        """百度财经日 / 周 / 月 K 线（含 MA5/MA10/MA20 指标，挂 ``extra``）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519`` / ``sz301086``）。
        period:
            ``day`` / ``week`` / ``month``。
        count:
            返回根数；超过 250 自动分页向前翻页收集。
        end_time:
            起始游标（unix 秒），用于断点续取。

        Returns
        -------
        旧→新排列的 ``list[Bar]``。MA 指标与换手率/涨跌幅挂到 ``extra``。
        """
        from .adapters_baidu import BaiduSource

        src = BaiduSource(client=_shared_http())
        try:
            return src.fetch_kline(symbol, period=period, count=count, end_time=end_time)
        finally:
            src.close()

    @staticmethod
    def baidu_minute(symbol: str) -> list[MinutePoint]:
        """百度财经当日 1 分钟分时（旧→新）。"""
        from .adapters_baidu import BaiduSource

        src = BaiduSource(client=_shared_http())
        try:
            return src.fetch_minute(symbol)
        finally:
            src.close()

    @staticmethod
    def baidu_ticks(symbol: str, *, limit: int = 200) -> list[Tick]:
        """百度财经当日逐笔成交（默认 200 条）。"""
        from .adapters_baidu import BaiduSource

        src = BaiduSource(client=_shared_http())
        try:
            return src.fetch_ticks(symbol, limit=limit)
        finally:
            src.close()

    @staticmethod
    def baidu_quote(symbol: str) -> Quote:
        """百度财经五档快照（含分时收盘价 / 均价 / 涨跌 / 五档盘口）。"""
        from .adapters_baidu import BaiduSource

        src = BaiduSource(client=_shared_http())
        try:
            return src.fetch_quote(symbol)
        finally:
            src.close()

    # -- 东财基金源（P0-1） ------------------------------------------------- #
    @staticmethod
    def fund_nav_history(
        code: str, *, page_size: int = 100, page_index: int = 1
    ) -> list[dict[str, Any]]:
        """东财基金历史净值（旧→新）。

        Returns
        -------
        ``list[dict]``，每条含 date / unit_nav / accum_nav / pct_change /
        bonus_ratio（净值日期、单位净值、累计净值、日增长率、分红/拆分）。
        """
        from .adapters_fund import FundSource

        src = FundSource(client=_shared_http())
        try:
            return src.fetch_nav_history(code, page_size=page_size, page_index=page_index)
        finally:
            src.close()

    @staticmethod
    def fund_estimate(code: str) -> dict[str, Any]:
        """东财基金实时估值快照（盘中估算）。

        Returns
        -------
        dict，含 code / name / jzrq / dwjz / gsz / gszzl / gztime。
        """
        from .adapters_fund import FundSource

        src = FundSource(client=_shared_http())
        try:
            return src.fetch_estimate(code)
        finally:
            src.close()

    @staticmethod
    def fund_list() -> list[dict[str, Any]]:
        """东财全量基金列表（约 1.2 万条）。

        Returns
        -------
        ``list[dict]``，每条含 code / name / type / pinyin / py_abbr。
        """
        from .adapters_fund import FundSource

        src = FundSource(client=_shared_http())
        try:
            return src.fetch_fund_list()
        finally:
            src.close()

    # -- 东财指数成分股（P0-2） --------------------------------------------- #
    @staticmethod
    def index_constituents(index: str) -> list[dict[str, Any]]:
        """东财指数成分股列表（分页拉全量）。

        Parameters
        ----------
        index:
            指数代码（``000300`` / ``000905`` / ``930050`` 等），可带市场前缀
            （``sh000300`` / ``sz399330`` / ``bj899050``）。

        Returns
        -------
        ``list[dict]``，每条含 code / name / secucode / weight / industry /
        region / price / change_pct / pe / eps / roe / bps / total_shares /
        free_shares / free_cap / type；``weight`` 仅部分指数族提供。
        """
        from .adapters_index import EastmoneyIndexConstituentsSource

        src = EastmoneyIndexConstituentsSource(client=_shared_http())
        try:
            return src.fetch_constituents(index)
        finally:
            src.close()

    @staticmethod
    def margin(
        symbol: str, *, days: int = 0, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """个股融资融券明细（东财 datacenter，``DATE`` 倒序）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519``）；须为两融标的，
            非标的返回空列表。
        days:
            取最近 N 个交易日（``0`` = 全部/按分页）。
        size:
            单页条数（``days=0`` 时生效）。

        Returns
        -------
        ``list[dict]``，每条含 date / code / name / market / rzye（融资余额，
        元）/ rzmre（融资买入）/ rzjme（融资净买）/ rqye（融券余额）/
        rqyl（融券余量，股）/ rzrqye（两融余额）/ rzyezb（融资余额占比%）/
        close / pct_change / total_mv 等；3/5/10 日差分字段在 ``extra``。
        """
        from .adapters_margin import EastmoneyMarginSource

        src = EastmoneyMarginSource(client=_shared_http())
        try:
            return src.fetch_margin(symbol, days=days, page=page, size=size)
        finally:
            src.close()

    # -- 代码联想 ----------------------------------------------------------- #
    @staticmethod
    def suggest(key: str, *, limit: int = 10) -> list[dict[str, str]]:
        """证券代码联想搜索（拼音 / 汉字 / 代码片段）。

        Returns
        -------
        ``[{"code": "600519", "name": "贵州茅台", "market": "sh",
        "symbol": "sh600519"}, ...]``
        """
        from .adapters_ext import SuggestSource

        src = SuggestSource(client=_shared_http())
        try:
            return src.fetch_suggest(key, limit=limit)
        finally:
            src.close()

    # -- 统一证券搜索 -------------------------------------------------------- #
    @staticmethod
    def search_symbols(
        pattern: str, *, limit: int = 10, market: str | None = None
    ) -> list[dict[str, str]]:
        """统一证券搜索（名称 / 拼音 / 代码片段 → 市场归属明确的候选列表）。

        与 :meth:`suggest` 同源（新浪联想接口），本方法在其上补齐
        ``display`` 展示名与 ``market`` 过滤语义，作为高层「搜代码」
        统一入口——先搜后查（行情 / K 线 / 基本面）的标准用法。

        Parameters
        ----------
        pattern:
            查询串：拼音（``maotai``）/ 汉字（``茅台``）/ 代码（``600519``）。
        limit:
            返回候选上限。
        market:
            可选市场过滤：``"sh"`` / ``"sz"`` / ``"bj"`` / ``"hk"`` / ``"us"``。

        Returns
        -------
        ``[{"code": "600519", "name": "贵州茅台", "market": "sh",
          "symbol": "sh600519", "display": "贵州茅台(600519)"}, ...]``
        """
        from .adapters_ext import SuggestSource

        src = SuggestSource(client=_shared_http())
        try:
            rows = src.fetch_suggest(pattern, limit=limit * 4 if market else limit)
        finally:
            src.close()
        want = str(market).lower() if market else None
        out: list[dict[str, str]] = []
        for r in rows:
            if want and r.get("market") != want:
                continue
            item = dict(r)
            item.setdefault("display", f"{r.get('name', '')}({r.get('code', '')})")
            out.append(item)
            if len(out) >= limit:
                break
        return out

    # -- 问财自然语言选股 ---------------------------------------------------- #
    @staticmethod
    def wencai(
        query: str, *, page: int = 1, limit: int = 50, cookie: str | None = None
    ) -> list[dict[str, Any]]:
        """i问财自然语言选股。

        Parameters
        ----------
        query:
            自然语言条件，如 ``"连板3板以上"`` / ``"macd金叉 量比大于2"``。
        page, limit:
            分页参数。
        cookie:
            hexin-v cookie；缺省读环境变量 ``TSTDX_WENCAI_COOKIE``。
            tstdx 不依赖任何第三方 cookie 中继服务——cookie 由调用方持有。

        Returns
        -------
        ``list[dict]``（表头与行 zip；空结果为 ``[]``）。
        """
        from .wencai import WencaiSource

        src = WencaiSource(cookie=cookie, client=_shared_http())
        try:
            return src.fetch_strategy(query, page=page, limit=limit)
        finally:
            src.close()

    # -- 指数列表 ------------------------------------------------------------ #
    @staticmethod
    def index_list() -> list[dict[str, str]]:
        """常用指数目录（名称 + 带市场代码，离线静态，无需网络）。

        与 :meth:`index`（行情）互补：先取目录再行情。
        """
        return [
            {"name": name, "symbol": sym, "market": sym[:2], "code": sym[2:]}
            for name, sym in INDEX_SYMBOLS.items()
        ]
