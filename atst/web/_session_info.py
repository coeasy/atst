# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（资金流 / 基本面）——P4 纯搬移拆分。

本模块只承载 :class:`atst.web.session.WebQuoteSession` 的方法**纯搬移**
（方法体逐字不变），按域拆为两个 Mixin：

* :class:`FundFlowSessionMixin`：资金流 / 涨跌停池 / 市场宽度 / 北向
* :class:`CorporateSessionMixin`：F10 / 公告 / 股东 / 解禁 / 业绩 / 龙虎榜 / 新闻

组合与 ``__init__`` / ``close`` 见 :mod:`atst.web.session`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅注解引用（future annotations 下零运行时开销）
    from .market_stats import LimitUpLadder, MarketBreadth

from ._session_market import _shared_http  # noqa: E402,F401  共享连接池助手

__all__ = [
    "FundFlowSessionMixin",
    "CorporateSessionMixin",
]


class FundFlowSessionMixin:
    """资金流 / 涨跌停池 / 市场宽度 / 盘中异动 / 北向资金。"""

    # -- 资金流 ------------------------------------------------------------- #
    @staticmethod
    def fund_flow(symbols: Sequence[str]) -> list[dict[str, Any]]:
        """当日实时资金流（主力 / 超大单 / 大单 / 中单 / 小单）。"""
        from .fundflow import EastmoneyFundFlowSource

        src = EastmoneyFundFlowSource(client=_shared_http())
        try:
            return src.fetch_flow(list(symbols))
        finally:
            src.close()

    @staticmethod
    def fund_flow_history(
        symbol: str, *, period: str = "day", count: int = 10
    ) -> list[dict[str, Any]]:
        """历史资金流序列（按时间升序）。"""
        from .fundflow import EastmoneyFundFlowSource

        src = EastmoneyFundFlowSource(client=_shared_http())
        try:
            return src.fetch_history(symbol, period=period, count=count)
        finally:
            src.close()

    @staticmethod
    def big_order_flow(symbol: str) -> dict[str, Any] | None:
        """个股大单流向（五档资金分档明细，语义别名）。

        与 :meth:`fund_flow`（批量）同通道（东财 ulist 资金流），
        本方法聚焦单只标的：主力 / 超大单 / 大单 / 中单 / 小单的
        净流入与占比，作为「大单流向」统一入口。

        Returns
        -------
        ``{"code","name","price","change_pct","main_net","main_net_ratio",
        "super_large_net","super_large_ratio","large_net","large_ratio",
        "medium_net","medium_ratio","small_net","small_ratio"}``；
        标的不存在或无数据时 ``None``。金额单位元、占比百分数。
        """
        from .fundflow import EastmoneyFundFlowSource

        src = EastmoneyFundFlowSource(client=_shared_http())
        try:
            rows = src.fetch_flow([symbol])
        finally:
            src.close()
        return rows[0] if rows else None

    @staticmethod
    def limit_pool(
        pool: str = "zt", *, date: str = "", page: int = 1, limit: int = 20
    ) -> list[dict[str, Any]]:
        """涨停 / 跌停 / 炸板池（``zt`` / ``dt`` / ``zb``）。"""
        from .fundflow import EastmoneyLimitPoolSource

        src = EastmoneyLimitPoolSource(client=_shared_http())
        try:
            return src.fetch_pool(pool, date=date, page=page, limit=limit)
        finally:
            src.close()

    @staticmethod
    def limit_up_ladder(*, date: str = "") -> LimitUpLadder:
        """涨停梯队 / 连板高度 / 炸板率聚合（S2）。

        复用东财 push2ex 涨停 / 跌停 / 炸板池（:meth:`limit_pool`），本地聚合：
        连板梯队分布、最高连板数、首板 / 连板家数、炸板率 = 炸板 / (涨停 + 炸板)。

        Parameters
        ----------
        date:
            ``YYYYMMDD``；空串表示最新交易日。

        Returns
        -------
        :class:`~atst.web.market_stats.LimitUpLadder`
        """
        from .fundflow import EastmoneyLimitPoolSource
        from .market_stats import aggregate_limit_pool

        pool = EastmoneyLimitPoolSource()
        try:
            zt = pool.fetch_pool("zt", date=date, limit=500)
            dt = pool.fetch_pool("dt", date=date, limit=500)
            zb = pool.fetch_pool("zb", date=date, limit=500)
            zt_total = zt[0]["total"] if zt else 0
            dt_total = dt[0]["total"] if dt else 0
            zb_total = zb[0]["total"] if zb else 0
            return aggregate_limit_pool(
                zt,
                dt,
                zb,
                zt_total=zt_total,
                dt_total=dt_total,
                zb_total=zb_total,
            )
        finally:
            pool.close()

    @staticmethod
    def market_breadth(*, market: str = "all_a", limit: int = 10000) -> MarketBreadth:
        """全市场涨跌宽度（S1）：上涨 / 下跌 / 平盘家数。

        数据来源：东财 clist 一次拉全市场（``fltt=2`` 原生涨跌幅），本地按
        ``change_pct`` 计数；涨停 / 跌停 / 炸板家数由涨跌停池补充。

        Parameters
        ----------
        market:
            市场过滤键（见 ``MARKET_FILTERS``），默认 ``all_a`` 沪深 A 股。
        limit:
            clist 单页拉取上限；默认 10000 足以覆盖全部 A 股（一次到位）。

        Returns
        -------
        :class:`~atst.web.market_stats.MarketBreadth`
        """
        from .fundflow import EastmoneyLimitPoolSource, EastmoneyRankSource
        from .market_stats import aggregate_breadth

        rank = EastmoneyRankSource()
        pool = EastmoneyLimitPoolSource()
        try:
            rows = rank.fetch_rows(market, sort="change_pct", limit=limit)
            zt = pool.fetch_pool("zt")
            dt = pool.fetch_pool("dt")
            zb = pool.fetch_pool("zb")
            zt_total = zt[0]["total"] if zt else 0
            dt_total = dt[0]["total"] if dt else 0
            zb_total = zb[0]["total"] if zb else 0
            return aggregate_breadth(
                rows,
                limit_up=zt_total,
                limit_down=dt_total,
                broken=zb_total,
            )
        finally:
            rank.close()
            pool.close()

    @staticmethod
    def stock_changes(
        types: Sequence[int] = (), *, page: int = 1, size: int = 50
    ) -> list[dict[str, Any]]:
        """盘中异动池（20 类异动，交易时段实时滚动）。

        Parameters
        ----------
        types:
            异动类型（:data:`~atst.web.fundflow.EastmoneyStockChangesSource.CHANGE_TYPES`
            的键，如 ``8201`` 火箭发射 / ``8193`` 大笔买入）；空 = 全部 20 类。
        page, size:
            分页。

        Returns
        -------
        ``[{"time": "11:19:05", "code": "600186", "market": 1,
        "name": "莲花控股", "change_type": 8202, "change_name": "快速反弹",
        "metrics": [...], "total": 2091}, ...]``

        ``metrics`` 为异动指标数值列表（含义随类型不同）；非交易时段
        返回 ``[]``（合法状态）。
        """
        from .fundflow import EastmoneyStockChangesSource

        src = EastmoneyStockChangesSource(client=_shared_http())
        try:
            return src.fetch_changes(tuple(types), page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def sina_fund_flow(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """新浪个股资金流历史（东财资金流的交叉验证源，按日倒序）。

        返回 ``[{"date","close","change_pct","net_amount","net_ratio",
        "super_large_net","super_large_ratio","industry_net",...}, ...]``；
        金额单位为**元**、比率为**百分数**。
        """
        from .fundflow import SinaFundFlowSource

        src = SinaFundFlowSource(client=_shared_http())
        try:
            return src.fetch_stock_flow(symbol, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def sina_board_fund_flow(
        board_code: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """新浪板块资金流历史（``board_code`` 形如 ``new_dzxx``，按日倒序）。

        板块代码取自新浪行业板块列表的 ``code`` 字段。
        """
        from .fundflow import SinaFundFlowSource

        src = SinaFundFlowSource(client=_shared_http())
        try:
            return src.fetch_board_flow(board_code, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def northbound() -> list[dict[str, Any]]:
        """沪深港通当日资金与额度（金额单位：万元）。"""
        from .fundflow import EastmoneyNorthboundSource

        src = EastmoneyNorthboundSource(client=_shared_http())
        try:
            return src.fetch_northbound()
        finally:
            src.close()


class CorporateSessionMixin:
    """F10 基本面 / 公告 / 股东 / 解禁 / 业绩 / 龙虎榜 / 新闻。"""

    # -- 基本面 ------------------------------------------------------------- #
    @staticmethod
    def profile(symbol: str) -> dict[str, Any]:
        """F10 基础资料（估值 / 股本 / 市值 / 52 周区间）。"""
        from .corporate import EastmoneyProfileSource

        src = EastmoneyProfileSource(client=_shared_http())
        try:
            return src.fetch_profile(symbol)
        finally:
            src.close()

    @staticmethod
    def notices(symbols: Sequence[str], *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """上市公司公告列表。"""
        from .corporate import EastmoneyNoticeSource

        src = EastmoneyNoticeSource(client=_shared_http())
        try:
            return src.fetch_notices(list(symbols), page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def reports(symbol: str = "", *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """个股研报（评级 / 盈利预测）；``symbol`` 为空表示全市场最新。"""
        from .corporate import EastmoneyResearchSource

        src = EastmoneyResearchSource(client=_shared_http())
        try:
            return src.fetch_reports(symbol, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def shareholders(symbol: str, *, size: int = 10) -> list[dict[str, Any]]:
        """十大流通股东。"""
        from .corporate import EastmoneyShareholderSource

        src = EastmoneyShareholderSource(client=_shared_http())
        try:
            return src.fetch_free_holders(symbol, size=size)
        finally:
            src.close()

    @staticmethod
    def free_holders(symbol: str, *, size: int = 10) -> list[dict[str, Any]]:
        """十大流通股东（主线兼容命名）。

        ``shareholders`` is the native session name, while ``free_holders`` is
        the established public capability used by the unified API and provider
        registry.  Keep both names on the same implementation so the runtime
        cannot advertise a capability that the web session cannot execute.
        """
        return CorporateSessionMixin.shareholders(symbol, size=size)

    @staticmethod
    def holder_num(symbol: str, *, size: int = 10) -> list[dict[str, Any]]:
        """股东户数变动历史。"""
        from .corporate import EastmoneyShareholderSource

        src = EastmoneyShareholderSource(client=_shared_http())
        try:
            return src.fetch_holder_num(symbol, size=size)
        finally:
            src.close()

    @staticmethod
    def block_trades(symbol: str = "", *, date: str = "", size: int = 20) -> list[dict[str, Any]]:
        """大宗交易明细；``symbol`` 为空表示全市场。"""
        from .corporate import EastmoneyBlockTradeSource

        src = EastmoneyBlockTradeSource(client=_shared_http())
        try:
            return src.fetch_block_trades(symbol=symbol, date=date, size=size)
        finally:
            src.close()

    @staticmethod
    def unlocks(
        symbol: str = "", *, begin: str = "", end: str = "", size: int = 20
    ) -> list[dict[str, Any]]:
        """限售股解禁日程（数量单位万股、市值单位万元）。"""
        from .corporate import EastmoneyUnlockSource

        src = EastmoneyUnlockSource(client=_shared_http())
        try:
            return src.fetch_unlocks(symbol=symbol, begin=begin, end=end, size=size)
        finally:
            src.close()

    @staticmethod
    def performance(
        symbol: str = "", *, report_date: str = "", size: int = 20
    ) -> list[dict[str, Any]]:
        """定期报告业绩指标（EPS / 营收 / 净利 / ROE / 毛利率）。"""
        from .corporate import EastmoneyPerformanceSource

        src = EastmoneyPerformanceSource(client=_shared_http())
        try:
            return src.fetch_performance(symbol=symbol, report_date=report_date, size=size)
        finally:
            src.close()

    @staticmethod
    def forecast(
        symbol: str = "", *, report_date: str = "", size: int = 20
    ) -> list[dict[str, Any]]:
        """业绩预告 / 快报日历（东财 ``RPT_PUBLIC_OP_NEWPREDICT``）。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀；空串表示全市场最新预告。
        report_date:
            报告期（``2026-06-30``）；空串表示最新一期。

        Returns
        -------
        ``[{"code","name","notice_date","report_date","predict_type",
        "profit_lower","profit_upper","amp_lower","amp_upper","content",
        "forecast_state","market"}, ...]``

        金额单位为元、变动幅度为百分数。
        """
        from .corporate import EastmoneyForecastSource

        src = EastmoneyForecastSource(client=_shared_http())
        try:
            return src.fetch_forecast(symbol=symbol, report_date=report_date, size=size)
        finally:
            src.close()

    @staticmethod
    def ipo_calendar(
        *, apply_date: str = "", page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """IPO 申购日历（东财 datacenter-web，申购日期降序）。

        Parameters
        ----------
        apply_date:
            申购日 ``YYYY-MM-DD``；非空即「今日申购」视图，空串全量分页。

        Returns
        -------
        ``[{"code","name","secucode","apply_code","apply_date",
        "listing_date","ballot_num_date","ballot_pay_date",
        "online_issue_date","trade_market","market_type","issue_price",
        "predict_issue_price","issue_num","online_issue_num",
        "online_apply_upper","top_apply_marketcap","industry_pe",
        "after_issue_pe","bvps","issue_way"}, ...]``

        未定价/未上市字段为 ``None``（不填充 0）；``issue_num`` 万股，
        申购数量单位为股，顶格市值单位为万元。
        """
        from .corporate import EastmoneyIpoSource

        src = EastmoneyIpoSource(client=_shared_http())
        try:
            return src.fetch_ipo(apply_date=apply_date, page=page, size=size)
        finally:
            src.close()

    # -- 龙虎榜 -------------------------------------------------------------- #
    @staticmethod
    def longhu(
        date: str | None = None,
        *,
        symbol: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """龙虎榜每日上榜个股（东财 datacenter-web）。

        Parameters
        ----------
        date:
            交易日 ``YYYY-MM-DD``（默认取最新一期）。
        symbol:
            限定单只标的（如 ``sh600519`` / ``600519``），可选。
        """
        from .longhu import EastmoneyTopListSource

        src = EastmoneyTopListSource(client=_shared_http())
        try:
            return src.fetch_lhb(date=date, symbol=symbol, page=page, size=size)
        finally:
            src.close()

    # -- 个股新闻 ----------------------------------------------------------- #
    @staticmethod
    def news(
        symbol: str,
        *,
        page: int = 1,
        num: int = 20,
        tag: str | None = None,
        pages: int = 1,
    ) -> list[dict[str, Any]]:
        """新浪个股新闻列表（标题 / 链接 / 时间）。

        Parameters
        ----------
        symbol:
            代码（``600519`` / ``sh600519`` / ``hk00700`` 等）。
        page:
            逻辑起始页码（从 1 开始）。
        num:
            单页条数上限。
        tag:
            仅取该分类标签的新闻（如 ``"研报"``）；``None`` 不过滤。
        pages:
            连续聚合的页数（默认 1）。``pages>1`` 时从 ``page`` 起逐页拉取并
            跨页去重拼接；某页为空或不足 ``num`` 条即提前结束（新浪服务端
            恒定返回约 40 条，超出部分无法再翻）。

        Returns
        -------
        ``[{"datetime": "2026-09-01 00:00", "title": "...",
          "url": "https://...", "tag": "研报" | ""}, ...]``
        """
        from .news import SinaNewsSource

        src = SinaNewsSource(client=_shared_http())
        try:
            if pages and pages > 1:
                out: list[dict[str, Any]] = []
                seen_keys: set[tuple[str, str]] = set()
                for p in range(int(page), int(page) + int(pages)):
                    chunk = src.fetch_news(symbol, page=p, num=num, tag=tag)
                    if not chunk:
                        break
                    for it in chunk:
                        k = (it["datetime"], it["title"])
                        if k not in seen_keys:
                            out.append(it)
                            seen_keys.add(k)
                    if len(chunk) < int(num):
                        break
                return out
            return src.fetch_news(symbol, page=page, num=num, tag=tag)
        finally:
            src.close()

    # -- 通用 datacenter 报表直查（P12 扩展） -------------------------------- #
    #: 各报表常用的个股过滤键（``dc_query(symbol=...)`` 自动构造 filter 用）。
    _DC_SYMBOL_FILTER_KEYS: dict[str, str] = {
        "performance": "SECURITY_CODE",
        "forecast": "SECURITY_CODE",
        "free_holders": "SECURITY_CODE",
        "holder_num": "SECURITY_CODE",
        "block_trade": "SECURITY_CODE",
        "unlock": "SECURITY_CODE",
        "dividend": "SECURITY_CODE",
        "rights_issue": "SECURITY_CODE",  # 配股（按 SECURITY_CODE 过滤）
        "executive_hold": "SECURITY_CODE",
        "shareholder_change": "SECURITY_CODE",
        "org_profile": "SECURITY_CODE",
        "rating_forecast": "SECURITY_CODE",
        "ipo": "",  # 无个股过滤键（全市场报表）
        # ---- P2 扩展（2026-09-11）：行业/概念/宏观/可转债 均为市场级报表 ----
        "industry_index": "",  # 行业指数（按 REPORT_DATE/BOARD_CODE 过滤）
        "concept_index": "",  # 概念指数（按 INDEX_CODE 过滤）
        "macro_cpi": "",  # 全国 CPI（按 REPORT_DATE 排序）
        "macro_ppi": "",  # 全国 PPI（按 REPORT_DATE 排序）
        "macro_gdp": "",  # 全国 GDP（按 REPORT_DATE 排序）
        "convertible_bonds": "",  # 可转债清单（按 LISTING_DATE 排序）
        # ---- P3 扩展（2026-09-11）：北向持股/十大股东/解禁股票/业绩预告 ----
        "northbound_hold": "SECURITY_CODE",  # 北向持股（按 SECURITY_CODE 过滤）
        "top_holders": "SECUCODE",  # 十大股东（按 SECUCODE 过滤，与 free_holders 一致）
        "unlock_stocks": "SECURITY_CODE",  # 解禁股票（按 SECURITY_CODE 过滤）
        "earnings_preview": "SECURITY_CODE",  # 业绩预告旧版（按 SECURITY_CODE 过滤）
    }

    @staticmethod
    def dc_reports() -> dict[str, str]:
        """可用 datacenter 报表白名单（``{别名: 报表名}``，实测可用名单）。

        白名单维护在 :data:`atst.web.corporate.VALID_REPORTS`；
        新增报表名前须重新抓包验证（东财会下线旧报表）。
        """
        from .corporate import VALID_REPORTS

        return dict(VALID_REPORTS)

    @staticmethod
    def dc_query(
        report: str,
        *,
        symbol: str = "",
        filters: Sequence[str] = (),
        sort_columns: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """通用 datacenter 报表直查（字段**原样透传**，单位以东财报表页为准）。

        Parameters
        ----------
        report:
            :meth:`dc_reports` 白名单键或原始报表名（非白名单名直接传服务端，
            服务端会拒绝已下线报表——报「报表配置不存在」）。
        symbol:
            可选个股代码（``600519`` / ``sh600519``）——按报表的个股过滤键
            （如 ``SECURITY_CODE="600519"``）自动构造 filter；不支持个股过滤
            的报表（如 ``ipo``）请用 ``filters``。
        filters:
            原始过滤子句（如 ``'REPORT_DATE="2025-12-31"'``）。
        sort_columns / page / size / all_pages / max_pages:
            排序与分页（语义同 :meth:`EastmoneyDataCenterSource.fetch_rows`）。

        Returns
        -------
        ``list[dict]``：datacenter 原始字段行（**不做单位翻译/重命名**——
        各报表字段语义差异大，翻译权留给调用方，避免误导）。
        """
        from .corporate import EastmoneyDataCenterSource

        flt: list[str] = list(filters)
        if symbol:
            key = CorporateSessionMixin._DC_SYMBOL_FILTER_KEYS.get(report, "SECURITY_CODE")
            if key:
                from ..domain.symbol import split_symbol

                _, code = split_symbol(symbol)
                flt.append(f'{key}="{code}"')
        src = EastmoneyDataCenterSource(report=report, client=_shared_http())
        try:
            return src.fetch_rows(
                filters=flt,
                sort_columns=sort_columns,
                page=page,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
            )
        finally:
            src.close()

    # -- V6 L3 新增源（2026-10 真机验证）----------------------------------- #
    #
    # 方法名与 capability 同名：能力发现面（``Client.capabilities()``）与
    # ``WebQuoteSession`` 的 facade 出口是同一份名单的两处出口，
    # tests/architecture/test_offline_capability_honesty.py 盯着它们不许分叉。
    # 因此这里不写 ``cninfo_announcements`` / ``ths_hot_rank`` 这类带源前缀的别名。

    @staticmethod
    def breaking_news(*, limit: int = 20, channel: str = "global-channel") -> list[dict[str, Any]]:
        """华尔街见闻全球快讯（最近 N 条）。

        快讯是全市场口径，没有「某只股票的快讯」这一说，因此不收 ``symbol``。
        """
        from .wallstreet.adapters import WallstreetSource

        src = WallstreetSource(client=_shared_http())
        try:
            return src.fetch_breaking_news(limit=limit, channel=channel)
        finally:
            src.close()

    @staticmethod
    def theme_attribution(
        symbol: str = "", *, date: str = "", limit: int = 20
    ) -> list[dict[str, Any]]:
        """同花顺涨停板块归属（板块维度聚合，含成分股）。

        Parameters
        ----------
        symbol:
            板块代码；为空返回全部板块。
        date:
            ``YYYYMMDD``；空串取最新交易日。
        """
        from .ths.adapters import ThsSource

        src = ThsSource(client=_shared_http())
        try:
            return src.fetch_theme_attribution(symbol or None, date=date or None, limit=limit)
        finally:
            src.close()

    @staticmethod
    def concept_members(
        symbol: str = "", *, date: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        """同花顺概念成分股（把板块归属里的成分摊平成个股清单）。"""
        from .ths.adapters import ThsSource

        src = ThsSource(client=_shared_http())
        try:
            return src.fetch_concept_members(symbol or None, date=date or None, limit=limit)
        finally:
            src.close()

    @staticmethod
    def hk_announcements(
        symbol: str = "", *, limit: int = 30, start_date: str = "", end_date: str = ""
    ) -> list[dict[str, Any]]:
        """巨潮港交所披露公告（``symbol`` 为空取全部港股）。"""
        from .cninfo.adapters import CninfoSource

        src = CninfoSource(client=_shared_http())
        try:
            return src.fetch_hk_announcements(
                symbol or None,
                limit=limit,
                start_date=start_date or None,
                end_date=end_date or None,
            )
        finally:
            src.close()
