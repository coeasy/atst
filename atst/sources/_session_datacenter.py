# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 的东财 datacenter 具名报表 Mixin（v2/W7 按领域拆分）。

``_dc_rows`` 是这一族的唯一取数器；报表名登记在
:data:`atst.sources.corporate.VALID_REPORTS`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._session_market import _shared_http


class DatacenterSessionMixin:
    @staticmethod
    def _dc_rows(
        report: str,
        filters: Sequence[str],
        *,
        sort_columns: str = "",
        sort_types: str = "-1",
        page: int = 1,
        size: int = 100,
        all_pages: bool = False,
        max_pages: int = 50,
        allow_empty: bool = True,
    ) -> list[dict[str, Any]]:
        """datacenter 报表取数的唯一出口（这一组能力共用）。"""
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report=report, client=_shared_http())
        try:
            return src.fetch_rows(
                filters=list(filters),
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                allow_empty=allow_empty,
            )
        finally:
            src.close()

    @staticmethod
    def _dc_symbol_filter(symbol: str) -> str | None:
        """个股 → 6 位代码；指数返回 ``None``（撞码守卫，见 ``report_code_for``）。

        返回 ``None`` 是**信号而不是错误**：调用方据此直接返回空集，一次 HTTP
        都不发——``sh000001``（上证指数）与 ``sz000001``（平安银行）在这些按
        6 位码过滤的报表里撞码，不挡就是把平安银行的事件安到指数头上。
        """
        from .corporate import report_code_for

        return report_code_for(symbol)

    @staticmethod
    def suspension(
        date: str,
        *,
        market: str = "全部",
        size: int = 200,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """停复牌信息（按**交易日**查询，不是按个股）。

        数据源 ``RPT_CUSTOM_SUSPEND_DATA_INTERFACE``。两个必须知道的口径：

        * **空 filter 会被服务端拒绝**（``9501 filter字段不能为空``），所以
          ``date`` 是必填。维度是交易日 ``DATETIME``——想查某只股票的停牌史，
          请拉多日全量后在本地按 ``SECURITY_CODE`` 筛。
        * ``MARKET`` 用中文面值（``全部`` / ``沪市`` / ``深市``）。

        回测里停牌是硬需求：停牌期间的 bar 不能当成「价格没动」，也不能拿
        假价格填。这条能力给的就是「哪天哪些票停了、为什么、停多久」。

        Returns
        -------
        ``list[dict]``，列名即报表字段名：``SECURITY_CODE`` /
        ``SECURITY_NAME_ABBR`` / ``SUSPEND_START_TIME`` / ``SUSPEND_END_TIME`` /
        ``SUSPEND_EXPIRE`` / ``SUSPEND_REASON`` / ``TRADE_MARKET`` /
        ``SUSPEND_START_DATE``。

        实测：``2026-10-13`` → 17 条，``2024-04-26`` → 927 条。
        """
        return DatacenterSessionMixin._dc_rows(
            "suspension",
            [f'MARKET="{market}"', f"DATETIME='{date}'"],
            sort_columns="SUSPEND_START_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def goodwill(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """个股商誉明细（按报告期）。

        数据源 ``RPT_GOODWILL_STOCKDETAILS``。**稀疏报表**：多数公司没有商誉，
        查不到是正常的空集而不是故障（``allow_empty=True``；600519 实测回
        ``code=9201 返回数据为空``）。商誉排雷时真正的信号是「商誉/净资产
        占比高」而不是「有商誉」，``RPT_GOODWILL_MARKETSTATISTICS``
        （见 :meth:`goodwill_market`）直接给了这个比值。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "goodwill",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="NOTICE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def goodwill_market(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """市场商誉统计（按板块 × 报告期，含商誉/净资产占比与减值状态）。

        数据源 ``RPT_GOODWILL_MARKETSTATISTICS``（实测 59 行，全市场按报告期
        汇总）。要「哪些板块商誉风险高」看这张，:meth:`goodwill` 是个股明细。
        """
        return DatacenterSessionMixin._dc_rows(
            "goodwill_market",
            [],
            sort_columns="REPORT_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def seo(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """增发 / 再融资（SEO）明细。

        数据源 ``RPT_SEO_DETAIL``。与 :meth:`rights_issue`（配股）合起来才是
        完整的股本扩张事件集——漏掉增发，除权价里的股本项就少一块。

        **不指定排序列**：实测 ``NOTICE_DATE`` / ``ISSUE_START_DATE`` /
        ``UPDATE_DATE`` 均回 ``9501 排序列不存在``，因此这里用服务端默认
        顺序。需要稳定顺序的调用方请自行按 ``ISSUE_DATE`` 排序。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "seo",
            [f'SECURITY_CODE="{code}"'],
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def equity_structure(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股本结构（有限售 / 无限售 / 总股本及占比，含变动原因）。

        数据源 ``RPT_F10_EH_EQUITY``。算真实换手率需要**流通股本**而不是
        总股本——这一列在这张表里（``LISTED_A_SHARES`` 等），按报告期给。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "equity_structure",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="END_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def holder_num_detail(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股东户数**明细**（按报告期，含环比变动与户均市值）。

        区别于 :meth:`holder_num`（``RPT_HOLDERNUMLATEST``，只给最新一期）——
        户数收敛/发散是筹码集中度的常用代理指标，要看趋势必须走这张
        （``RPT_HOLDERNUM_DET``）。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "holder_num_detail",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="END_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def org_seat_trades(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """机构专用席位交易明细（龙虎榜里的「机构专用」席位）。

        数据源 ``RPT_ORGANIZATION_TRADE_DETAILS``。与 :meth:`longhu`
        （龙虎榜）互补：龙虎榜给当日全部席位，这张只给机构席位，适合判断
        「这波是机构在买还是游资在炒」。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "org_seat_trades",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def connect_deals(
        *,
        mutual_type: str = "",
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """沪深港通历史成交（逐日：净流入 / 成交额 / 额度余额 / 领涨股）。

        数据源 ``RPT_MUTUAL_DEAL_HISTORY``。**南北双向都在这一张表里**，
        ``mutual_type`` 给空串表示不过滤（六种通道全要）。

        ``MUTUAL_TYPE`` 的编号语义（2026-10-14 实测，按 ``LEAD_STOCKS_CODE``
        的交易所后缀判定，不是按命名猜的）：

        ====  ======================  ==========  =====================
        编号  通道                     方向        领涨股后缀（实测）
        ====  ======================  ==========  =====================
        001   沪股通                   北向        ``.SH``（A 股）
        002   沪市港股通               南向        ``.HK``
        003   深股通                   北向        ``.SZ``（A 股）
        004   深市港股通               南向        ``.HK``
        005   北向合计（沪股通+深股通）  北向        ``.SH``
        006   南向合计（沪+深港股通）   南向        ``.HK``
        ====  ======================  ==========  =====================

        只看外资买 A 股请传 ``mutual_type="001"``（沪股通）/
        ``"003"``（深股通）/ ``"005"``（北向合计）——不传会混入南向数据，
        净流入的方向含义正好相反。
        （沪股通/深股通/港股通全要）；常见的 ``001`` 是沪股通。

        与既有 :meth:`northbound`（当日快照）和 :meth:`northbound_hold`
        （持股明细）分工：这条是**逐日成交流水**，能做时间序列。
        """
        filters = [f'MUTUAL_TYPE="{mutual_type}"'] if mutual_type else []
        return DatacenterSessionMixin._dc_rows(
            "connect_deals",
            filters,
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def southbound_ranks(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """**港股通（南向）**持股排行榜——按港股个股，含持股数/市值/占比与区间变动。

        数据源 ``RPT_MUTUAL_STOCK_HOLDRANKS``。

        .. warning::

           这张表**只有港股**，不是北向。实测全量样本 500 行里 ``SECUCODE``
           后缀 100% 是 ``.HK``，``MUTUAL_TYPE`` 只有 ``002``（沪市港股通）
           与 ``004``（深市港股通）——两个都是**南向**通道。A 股标的查这张表
           恒为空集（``SECURITY_CODE="600519"`` / ``SECUCODE="600519.SH"``
           实测均 0 行）。要看北向（外资持 A 股）请用 :meth:`northbound_hold`
           （``RPT_MUTUAL_HOLD``）。早先按表名里的 ``MUTUAL`` 想当然叫它
           ``northbound_ranks``，是错的——名字错了，调用方会拿着南向数据当
           北向用，方向正好相反。

        与 :meth:`northbound_hold` 的分工：那条是持仓明细，这里带
        ``HOLD_MARKETCAP_CHG1/5/10``（1/5/10 日市值变动）与
        ``PARTICIPANT_NUM``（参与机构数），适合做「南向在加仓还是减仓」。

        日期字段是 ``HOLD_DATE``；用 ``TRADE_DATE`` 过滤会报
        ``9501 filter字段中日期参数格式错误``。
        """
        from ..domain.symbol import split_symbol

        # 这里**不走** ``report_code_for``：那是指数/深市撞码守卫，而本表只
        # 收港股代码，用它反而会把合法港股挡在门外。
        _, code = split_symbol(symbol)
        return DatacenterSessionMixin._dc_rows(
            "southbound_ranks",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="HOLD_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def margin_daily(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """融资融券每日交易（市场级：融资余额 / 融券余额 / 两融余额）。

        数据源 ``RPTA_WEB_MARGIN_DAILYTRADE``（实测 3405 行，按
        ``STATISTICS_DATE`` 排序）。与既有 :meth:`margin`（个股两融明细）
        互补：这条是**全市场每日汇总**，看杠杆水位的时间序列用它。
        """
        return DatacenterSessionMixin._dc_rows(
            "margin_daily",
            [],
            sort_columns="STATISTICS_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def block_trade_stats(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """大宗交易统计（按个股 × 交易日汇总，含折溢价率）。

        数据源 ``RPT_BLOCKTRADE_STA``。与既有 :meth:`block_trades`
        （``RPT_DATA_BLOCKTRADE`` 逐笔明细）分工：这条是**已按日聚合**的
        统计（笔数/总量/总额/均价/折溢价），省得自己 groupby。
        """
        code = DatacenterSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return DatacenterSessionMixin._dc_rows(
            "block_trade_stats",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    # -- 宏观补齐（2026-10-14）--------------------------------------------- #

    @staticmethod
    def _macro_rows(
        report: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """宏观报表的共用取数器：全部按 ``REPORT_DATE`` 降序（最新在前）。"""
        return DatacenterSessionMixin._dc_rows(
            report,
            [],
            sort_columns="REPORT_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def macro_money_supply(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """货币供应量 M0/M1/M2（含同比、环比）。

        ``RPT_ECONOMY_CURRENCY_SUPPLY``，实测 225 期。M1-M2 剪刀差是常用的
        流动性观测口径，字段里 ``BASIC_CURRENCY``（M0）/ ``CURRENCY``（M1）
        之外还有 ``FREE_CASH``（M2）。
        """
        return DatacenterSessionMixin._macro_rows(
            "macro_money_supply", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_loan(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """人民币贷款（含同比、环比、累计）。``RPT_ECONOMY_RMB_LOAN``，225 期。"""
        return DatacenterSessionMixin._macro_rows(
            "macro_loan", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_retail(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """社会消费品零售总额（含同比、环比、累计）。

        ``RPT_ECONOMY_TOTAL_RETAIL``，209 期。
        """
        return DatacenterSessionMixin._macro_rows(
            "macro_retail", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_asset_invest(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """固定资产投资（含同比、环比、累计）。``RPT_ECONOMY_ASSET_INVEST``，161 期。"""
        return DatacenterSessionMixin._macro_rows(
            "macro_asset_invest", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_industrial(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """工业增加值（同比、累计）。``RPT_ECONOMY_INDUS_GROW``，205 期。"""
        return DatacenterSessionMixin._macro_rows(
            "macro_industrial", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_reserve_ratio(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """存款准备金率（大型/中小金融机构，含变动前后与生效日）。

        ``RPT_ECONOMY_DEPOSIT_RESERVE``，58 期。降准是 A 股最直接的流动性
        事件之一，``INTEREST_RATE_BB``/``BA`` 是调整前/后的大型机构档。
        """
        return DatacenterSessionMixin._macro_rows(
            "macro_reserve_ratio", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_forex_reserve(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """外汇储备 / 黄金储备（含同比、环比）。``RPT_ECONOMY_GOLD_CURRENCY``，225 期。"""
        return DatacenterSessionMixin._macro_rows(
            "macro_forex_reserve", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

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
            （见 :data:`~atst.sources.fundflow.MARKET_FILTERS`）。
        sort:
            ``change_pct`` / ``amount`` / ``turnover`` / ``main_net``
            / ``main_net_ratio`` 等（见
            :data:`~atst.sources.fundflow.SORT_FIELDS`）。
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
            :data:`atst.sources.fundflow.SORT_FIELDS`）；资金流排序自动附带
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
