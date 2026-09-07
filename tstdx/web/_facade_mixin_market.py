# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（行情 / K 线 / 板块）——P4 纯搬移拆分。

本模块只承载 :class:`tstdx.web.facade.WebQuoteSession` 的方法**纯搬移**
（方法体逐字不变），按域拆为三个 Mixin：

* :class:`QuoteSessionMixin`：实时行情 / 港美股 / 全市场 / 大盘统计 / 指数 / 汇率
* :class:`KlineSessionMixin`：K 线 / 分时 / 历史行情 / 逐笔成交
* :class:`BoardSessionMixin`：板块 / 排行 / 人气榜

组合与 ``__init__`` / ``close`` 见 :mod:`tstdx.web.facade`。
模块级共享常量（``SOURCE_ALIASES`` 除外）与进程级 HTTP 连接池助手也
收口于此，由 facade 再导出以保持公开 API 不变。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.models import Bar, MinutePoint, Quote
from ..errors import CompatibilityError
from .base import normalize_symbol
from .sources import BOC, SINA, TENCENT

__all__ = [
    "QuoteSessionMixin",
    "KlineSessionMixin",
    "BoardSessionMixin",
    "KLINES_PERIOD_ALIASES",
    "INDEX_SYMBOLS",
    "shared_http",
]

#: K 线周期别名 → tstdx period（KlineSource）。
KLINES_PERIOD_ALIASES: dict[str, str] = {
    "day": "day",
    "d": "day",
    "week": "week",
    "w": "week",
    "month": "month",
    "m": "month",
    "m1": "1min",
    "1min": "1min",
    "1m": "1min",
    "m5": "5min",
    "5min": "5min",
    "5m": "5min",
    "m15": "15min",
    "15min": "15min",
    "m30": "30min",
    "30min": "30min",
    "m60": "60min",
    "60min": "60min",
    "60m": "60min",
}

#: 常用大盘指数代码（用于 :meth:`WebQuoteSession.index`）。
INDEX_SYMBOLS: dict[str, str] = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "沪深300": "sh000300",
}

#: B6：进程级共享 HTTP 连接池（惰性创建）——门面各方法按调用新建的
#: 轻量源实例统一注入该 client，消除「每次调用新建 + 关闭」的 TCP 开销。
#: client 归进程所有：源实例 ``close()`` 对注入 client 不生效（见
#: ``BaseWebSource._owns_client``），连接随 keep-alive 池复用。
_SHARED_HTTP: list[Any] = []


def shared_http():
    """返回进程级共享 :class:`HttpClient`（线程安全惰性单例）。"""
    if not _SHARED_HTTP:
        from .base import build_client

        _SHARED_HTTP.append(build_client())
    return _SHARED_HTTP[0]


# 旧名别名：facade 内部沿用原私有名。
_shared_http = shared_http


class QuoteSessionMixin:
    """实时行情 / 港美股 / 全市场快照 / 大盘统计 / 指数 / 汇率。"""

    # -- 实时行情 ----------------------------------------------------------- #
    def quotes(self, codes: Sequence[str], *, prefix: bool = True) -> list[Quote]:
        """查询指定标的的实时行情，返回 ``list[Quote]``。

        Parameters
        ----------
        codes:
            6 位代码（``000001``）或带市场前缀（``sh600519``）。
        prefix:
            是否按 ``{market}{code}`` 全前缀归一（默认 True）。
        """
        if isinstance(codes, str):
            codes = [codes]
        symbols = [self._normalize(c) for c in codes]
        if self.source_name == BOC:
            raise CompatibilityError(
                "中行汇率源不提供 quotes()，请改用 rates()", context={"source": BOC}
            )
        try:
            return self._c.fetch(symbols)
        except Exception as exc:  # noqa: BLE001
            from ..errors import TdxError

            if isinstance(exc, TdxError):
                raise
            raise CompatibilityError(
                f"底层源调用失败: {exc}", context={"source": self.source_name}, cause=exc
            ) from exc

    # -- 港股 / 美股 -------------------------------------------------------- #
    @staticmethod
    def hk_quotes(codes: Sequence[str], *, provider: str = "tencent") -> list[Quote]:
        """港股实时行情。

        Parameters
        ----------
        codes:
            港股代码，如 ``["00700"]`` / ``["hk00700"]`` / ``["腾讯"]``（联想结果）。
        provider:
            ``"tencent"``（默认，qt.gtimg.cn）或 ``"sina"``（hq.sinajs.cn）。

        返回价格 / 成交额以原生币种港元(HKD)计，``extra["currency"]`` 标记；
        成交量单位为「股」（已与全局契约对齐）。
        """
        from .adapters import HkSource, SinaHkSource

        src_cls = SinaHkSource if provider == "sina" else HkSource
        src = src_cls(client=_shared_http())
        try:
            seq = [codes] if isinstance(codes, str) else list(codes)
            return src.fetch(seq)
        finally:
            src.close()

    @staticmethod
    def us_quotes(codes: Sequence[str]) -> list[Quote]:
        """美股实时行情（腾讯 qt.gtimg.cn q=usXXXXX）。

        Parameters
        ----------
        codes:
            美股代码，如 ``["AAPL"]`` / ``["usAAPL"]``。

        返回价格 / 成交额以原生币种美元(USD)计，``extra["currency"]`` 标记；
        成交量单位为「股」（已与全局契约对齐）。
        """
        from .adapters import UsSource

        src = UsSource(client=_shared_http())
        try:
            seq = [codes] if isinstance(codes, str) else list(codes)
            return src.fetch(seq)
        finally:
            src.close()

    # -- 全市场快照 --------------------------------------------------------- #
    def all_market(
        self,
        *,
        node: str = "hs_a",
        page_size: int = 80,
        max_pages: int | None = None,
    ) -> list[Quote]:
        """分页拉取全市场行情摘要（新浪 / 腾讯）。

        新浪 ``Market_Center.getHQNodeData`` 接口直接返回行情摘要；腾讯走
        ``getBoardRankList`` 枚举代码 + ``qt.gtimg.cn`` 批量行情（U5），
        无需先取代码表再逐批查询。

        Parameters
        ----------
        node:
            新浪市场节点：``hs_a`` 沪深 A 股 / ``hs_b`` B 股 / ``cyb`` 创业板
            / ``sh_a`` 沪 A / ``sz_a`` 深 A 等；腾讯支持 ``hs_a`` / ``cyb``。
            **港股（``hk``）/ 美股（``us``）整市场枚举未验证，显式拒绝**——
            需要港股/美股行情请用 :meth:`hk_quotes` / :meth:`us_quotes`
            （按代码批量）或 :meth:`klines` / :meth:`minute_klines`（K 线）。
        page_size:
            每页条数（新浪上限 100；腾讯上限 200）。
        max_pages:
            页数上限（防失控）；``None`` 表示拉到底。
        """
        if node.lower() in ("hk", "us", "hk_main", "us_main"):
            raise ValueError(
                f"all_market: 港股/美股整市场枚举未实装（N7 标注），node={node!r}；"
                "请用 hk_quotes()/us_quotes() 按代码批量取行情，或 klines()/minute_klines() 取 K 线"
            )
        if self.source_name in (SINA, TENCENT):
            return self._c.fetch_all(node=node, page_size=page_size, max_pages=max_pages)
        raise CompatibilityError(
            f"全市场快照 all_market() 仅新浪/腾讯源支持；当前源 {self.source_name!r}，"
            f"请改用 web_session('sina') 或 web_session('tencent').all_market()",
            context={"source": self.source_name},
        )

    # -- 外盘 / 大盘统计 ----------------------------------------------------- #
    @staticmethod
    def globals(codes: Sequence[str] | None = None) -> list[Quote]:
        """外盘行情（贵金属 / 能源 / 有色 / 农产品）。

        Parameters
        ----------
        codes:
            ``["CL", "GC"]``；``None`` 表示全部 13 个可用品种。
            可用代码见 :data:`~tstdx.web.global_market.GLOBAL_CODES`。
        """
        from .global_market import TencentGlobalSource

        src = TencentGlobalSource(client=_shared_http())
        try:
            return src.fetch(list(codes) if codes else [])
        finally:
            src.close()

    @staticmethod
    def market_stat(codes: Sequence[str] | None = None) -> list[Quote]:
        """大盘统计（点位 + 全市场成交量/额 + 总市值）。

        ``codes`` 形如 ``["sh000001", "sz399001"]``；``None`` 表示主要指数。
        """
        from .global_market import TencentMarketStatSource

        src = TencentMarketStatSource(client=_shared_http())
        try:
            return src.fetch(list(codes) if codes else [])
        finally:
            src.close()

    # -- 大盘指数 ----------------------------------------------------------- #
    def index(self) -> list[Quote]:
        """常用大盘指数行情（上证 / 深成 / 创业板 / 沪深 300）。"""
        return self.quotes(list(INDEX_SYMBOLS.values()))

    # -- 外汇牌价 ----------------------------------------------------------- #
    def rates(self) -> list[dict[str, Any]]:
        """中国银行外汇牌价。"""
        from .adapters import BocSource

        src = BocSource(**self._kwargs, client=_shared_http())
        try:
            return src.fetch_rates()
        finally:
            src.close()  # P1 #14: 释放连接池

    # -- 辅助 --------------------------------------------------------------- #
    @staticmethod
    def _normalize(code: str) -> str:
        # 委托统一符号引擎（单一事实源），不再手动拼接市场前缀，
        # 避免与 domain.symbol 推断逻辑漂移（P1 #9）
        try:
            return normalize_symbol(code)
        except Exception:  # noqa: BLE001
            return str(code).strip().lower()


class KlineSessionMixin:
    """K 线 / 当日分时 / 历史行情 / 逐笔成交。"""

    # -- K 线 --------------------------------------------------------------- #
    def klines(
        self,
        symbol: str,
        period: str = "day",
        *,
        count: int = 320,
        adjust: str = "qfq",
    ) -> list[Bar]:
        """日 / 周 / 月 / 分钟 K 线，返回 ``list[Bar]``。

        分钟周期（``m1``/``m5``/``m15``/``m30``/``m60``）：A 股走腾讯
        mkline；港股 / 美股走东财 push2his（腾讯 mkline 不支持 hk/us）。
        日线及以上走腾讯 fqkline（hk/us 亦支持）。

        Parameters
        ----------
        symbol:
            6 位代码（``600519``）或带前缀（``sh600519`` / ``hk00700``
            / ``usAAPL``）。
        period:
            ``day`` / ``week`` / ``month`` / ``m1`` / ``m5`` / ``m15``
            / ``m30`` / ``m60``（见 :data:`KLINES_PERIOD_ALIASES`）。
        """
        p = KLINES_PERIOD_ALIASES.get(str(period).lower())
        if p is None:
            # P1 #13: 未知周期显式报错，不再静默回退 day（周/月请求被偷换成
            # 日线属于「帧合法但内容错误」级缺陷）
            raise ValueError(f"未知 K 线周期 {period!r}；可选: {sorted(KLINES_PERIOD_ALIASES)}")
        if p.endswith("min"):
            from ..domain.symbol import parse_symbol

            mkt = parse_symbol(symbol).market
            # src 在三分支被赋不同源类型——显式 Any 化统一推断
            src: Any
            if mkt in ("hk", "us"):
                from .history import EastmoneyHistoryKlineSource

                src = EastmoneyHistoryKlineSource(client=_shared_http())
                try:
                    # 东财 push2his 是 hk/us 分钟 K 线唯一数据源：
                    # 港股 secid=116.x 确定性映射；美股 105/106/107 逐个探测。
                    return src.fetch_bars(symbol, period=p, count=count, adjust=adjust)
                finally:
                    src.close()
            from .adapters_ext import MinuteKlineSource

            src = MinuteKlineSource(client=_shared_http())
            try:
                # A 股分钟 K 线走腾讯 mkline（量「手」×100 到股）。
                return src.fetch_bars(symbol, period=p, count=count)
            finally:
                src.close()

        # 方法内导入：保留测试对 tstdx.web.adapters.KlineSource 的注入 seam
        from .adapters import KlineSource as _KlineSource

        src = _KlineSource(client=_shared_http())
        try:
            return src.fetch_bars(symbol, period=p, count=count, adjust=adjust)
        finally:
            src.close()

    # -- 当日分时 ----------------------------------------------------------- #
    def minute(self, symbol: str) -> list[MinutePoint]:
        """当日 1 分钟分时（价格 / 分钟增量成交量 / 增量成交额）。"""
        from .adapters_ext import MinuteSource

        src = MinuteSource(client=_shared_http())
        try:
            return src.fetch_minute(symbol)
        finally:
            src.close()

    # -- 历史行情 ----------------------------------------------------------- #
    @staticmethod
    def history(
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "qfq",
        source: str = "sina",
    ) -> list[Bar]:
        """历史 K 线（新浪 / 东财双源；东财含成交额与复权选项）。

        Parameters
        ----------
        source:
            ``"sina"``（5/15/30/60/120/240/1200 scale）或
            ``"eastmoney"``（5min–day，支持 adjust="" / "qfq" / "hfq"）。
        """
        from .history import EastmoneyHistoryKlineSource, SinaHistoryKlineSource

        src: Any
        # 深审 M9：新浪只提供原始价——复权请求自动路由到东财源
        if source == "eastmoney" or adjust not in ("", None):
            src = EastmoneyHistoryKlineSource(client=_shared_http())
            try:
                return src.fetch_bars(symbol, period=period, count=count, adjust=adjust)
            finally:
                src.close()
        src = SinaHistoryKlineSource(client=_shared_http())
        try:
            return src.fetch_bars(symbol, period=period, count=count, adjust=adjust)
        finally:
            src.close()

    # -- 逐笔成交 ----------------------------------------------------------- #
    @staticmethod
    def ticks(symbol: str, *, max_pages: int = 1) -> list[Any]:
        """当日逐笔成交明细（腾讯，每页 70 条）。

        Returns
        -------
        ``list[Tick]``：``volume`` 为股、``price`` 为元、``buyorsell``
        为 0=买 / 1=卖 / 2=中性。
        """
        from .ticks import TencentTickSource

        src = TencentTickSource(client=_shared_http())
        try:
            return src.fetch_ticks(symbol, max_pages=max_pages)
        finally:
            src.close()

    @staticmethod
    def intraday(symbol: str) -> list[MinutePoint]:
        """当日 1 分钟分时成交（东财，含均价；分钟数多于腾讯分时）。"""
        from .ticks import EastmoneyTrendsSource

        src = EastmoneyTrendsSource(client=_shared_http())
        try:
            return src.fetch_minutes(symbol)
        finally:
            src.close()


class BoardSessionMixin:
    """板块 / 排行 / 人气榜。"""

    # -- 板块 --------------------------------------------------------------- #
    @staticmethod
    def industry_boards() -> list[dict[str, Any]]:
        """新浪行业板块列表（49 行业，含领涨股与板块成交量/额）。"""
        from .boards import SinaIndustryBoardSource

        src = SinaIndustryBoardSource(client=_shared_http())
        try:
            return src.fetch_boards()
        finally:
            src.close()

    @staticmethod
    def board_list(board: str = "concept") -> list[dict[str, Any]]:
        """新浪板块列表（``concept`` 概念约 175 个 / ``region`` 地域 31 个
        / ``industry`` 新版行业 84 个，含领涨股与板块量额统计）。

        与 :meth:`industry_boards`（旧口径 49 行业）、:meth:`board_rank`
        （腾讯排行）、:meth:`em_boards`（东财）互补。

        Returns
        -------
        ``[{"code": "gn_hwqc", "name": "华为汽车", "count": 97,
        "pct_change": -1.02, "volume": 1946565402, "amount": 34277211962,
        "leader": "sh605068", "leader_name": "明新旭腾"}, ...]``
        ``code`` 可直接用于 :meth:`board_members` 拉成分。
        """
        from .boards import SinaBoardListSource

        src = SinaBoardListSource(client=_shared_http())
        try:
            return src.fetch_boards(board)
        finally:
            src.close()

    @staticmethod
    def board_members(
        node: str, *, page_size: int = 100, max_pages: int | None = None
    ) -> list[Quote]:
        """新浪板块成分行情（node 来自 industry_boards / board_list 的 code 字段）。"""
        from .boards import SinaBoardMemberSource

        src = SinaBoardMemberSource(client=_shared_http())
        try:
            return src.fetch_members(node, page_size=page_size, max_pages=max_pages)
        finally:
            src.close()

    @staticmethod
    def board_rank(
        board: str = "industry", *, limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """腾讯板块排行（industry/concept/region，含领涨股与 5/20 日涨幅）。"""
        from .boards import TencentBoardRankSource

        src = TencentBoardRankSource(client=_shared_http())
        try:
            return src.fetch_boards(board, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def em_boards(
        board: str = "industry", *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """东财板块列表（industry/concept/region，代码形如 BK0475）。"""
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_boards(board, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def em_board_members(
        board_code: str, *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """东财板块成分（board_code 形如 BK0475）。"""
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_members(board_code, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def stock_boards(symbol: str) -> list[dict[str, Any]]:
        """个股所属板块（行业/概念/地域全量，按板块涨跌幅降序）。

        与 :meth:`em_board_members`（板块 → 成分）方向互补：
        个股 → 所属板块。返回的 ``code``（BKxxxx）可直接回查
        :meth:`em_board_members` 拉成分，形成板块网络遍历。

        Returns
        -------
        ``[{"code": "BK1102", "name": "空气能热泵", "pct_change": 0.84,
        "market": 90}, ...]``
        """
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_stock_boards(symbol)
        finally:
            src.close()

    # -- 排行 --------------------------------------------------------------- #
    @staticmethod
    def rank(
        market: str = "all_a",
        *,
        sort: str = "change_pct",
        limit: int = 20,
        page: int = 1,
        extra_fields: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """通用排行（个股 / ETF / 指数 / 板块）。

        Parameters
        ----------
        market:
            ``all_a`` / ``sh_a`` / ``sz_a`` / ``gem`` / ``star`` / ``bse``
            / ``etf`` / ``index`` / ``industry`` / ``concept`` / ``region``
            （见 :data:`~tstdx.web.fundflow.MARKET_FILTERS`）。
        sort:
            ``change_pct`` / ``amount`` / ``turnover`` / ``main_net``
            / ``main_net_ratio`` 等（见
            :data:`~tstdx.web.fundflow.SORT_FIELDS`）。
        extra_fields:
            追加请求的东财原始字段（如 ``["f62", "f184"]`` 取资金流）。
        """
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(
                market, sort=sort, limit=limit, page=page, extra_fields=extra_fields
            )
        finally:
            src.close()

    @staticmethod
    def hot_boards(limit: int = 10) -> list[dict[str, Any]]:
        """行业板块资金流排行（按主力净流入降序）。"""
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(
                "industry",
                sort="main_net",
                limit=limit,
                extra_fields=["f62", "f184", "f66", "f72", "f78", "f84"],
            )
        finally:
            src.close()

    @staticmethod
    def sector_flow(
        board: str = "industry", *, sort: str = "main_net", limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块资金流排行（东财 push2 clist；金额单位元）。

        Parameters
        ----------
        board:
            ``industry`` 行业（默认）/ ``concept`` 概念 / ``region`` 地域。
        sort:
            ``main_net`` 主力净流入（默认）/ ``main_ratio`` 主力净占比 /
            ``change_pct`` 涨跌幅 / ``amount`` 成交额 等（见
            :data:`tstdx.web.fundflow.SORT_FIELDS`）；资金流排序自动附带
            f62/f184/f66/f72/f78/f84 字段（P12 修复：否则行值静默为 None）。

        Returns
        -------
        ``list[dict]``：code / name / symbol / main_net / main_net_ratio /
        super_large_net / large_net / medium_net / small_net / price /
        change_pct / amount 等。
        """
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(board, sort=sort, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def hot_rank(*, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """股吧个股人气榜（按名次升序，100 条/页）。

        Returns
        -------
        ``[{"rank": 1, "symbol": "sh600127", "code": "600127",
        "market": "sh", "rank_change": 0, "his_rank_change": 0}, ...]``

        榜单仅含排名与代码；如需行情请以 ``symbol`` 回查 :meth:`quotes`。
        """
        from .hot_rank import EastmoneyHotRankSource

        src = EastmoneyHotRankSource(client=_shared_http())
        try:
            return src.fetch_hot_rank(page=page, size=size)
        finally:
            src.close()
