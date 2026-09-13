# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（三大报表 / 治理 / 评级预测）——P13 数据源补全。

本模块承载 :class:`tstdx.web.facade.WebQuoteSession` 的**基本面深度**方法：

* 三大财务报表明细（资产负债表 / 利润表 / 现金流量表，
  :mod:`tstdx.web.fin_report`，东财 datacenter-web 报表族）
* 治理数据（董监高持股 / 股东增减持 / 公司概况，:mod:`tstdx.web.governance`）
* 卖方一致预期（券商评级 / 目标价 / EPS 预测，:mod:`tstdx.web.governance`）

补 :doc:`/docs/stock_analysis_prompt_coverage` 审计中的 P0 缺口——
「财务健康度与排雷」「核心风险揭示」「治理与供应链」「估值合理性」「市场情绪」
五个维度此前只有摘要口径、缺报表级明细。

与 :class:`CorporateSessionMixin` 的组合关系：本 Mixin 依赖相同的
:func:`_shared_http` 连接池与 ``try / finally close()`` 生命周期约定，
但方法体独立（不同后端 / 不同过滤键），不做继承耦合。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._facade_mixin_market import _shared_http

__all__ = ["FundamentalSessionMixin"]


class FundamentalSessionMixin:
    """三大报表 / 治理 / 评级预测。"""

    # -- 三大财务报表 ------------------------------------------------------- #
    @staticmethod
    def balance_sheet(
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """资产负债表明细（东财 F10，按报告期降序）。

        应收账款 / 长期应收款坏账风险、有息负债结构、商誉占净资产比等
        排雷分析的结构化入口。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀（``600519`` / ``sh600519``）；空串表示全市场最新一期。
        report_date:
            报告期（``2024-12-31``）；空串表示最新一期起分页。
        size:
            单页条数（上限 50）。
        all_pages:
            ``True`` 时拉全历史（受 ``max_pages`` 防御上限约束）。
        raw:
            ``True`` 时每条附原始行（键 ``raw``，全部报表列原样透传）。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "total_assets","total_liab","total_equity","monetary_funds",
        "accounts_recv","long_recv","inventory","goodwill","fixed_asset",
        "long_eq_invest","short_loan","long_loan","minority_equity"}, ...]``
        （金额单位元、``float`` 或 ``None``）
        """
        from .fin_report import EastmoneyF10ReportSource

        src = EastmoneyF10ReportSource(client=_shared_http())
        try:
            return src.fetch_balance_sheet(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            )
        finally:
            src.close()

    @staticmethod
    def income_sheet(
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """利润表明细（东财 F10，按报告期降序）。

        研发费用占收入比、三费结构、净利率与少数股东损益占比等
        盈利质量分析入口。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "operate_income","operate_cost","sell_expense","manage_expense",
        "rd_expense","finance_expense","profit_total","net_profit",
        "parent_net_profit","minority_profit","basic_eps","diluted_eps"}, ...]``
        （金额单位元、EPS 单位元/股、``float`` 或 ``None``）
        """
        from .fin_report import EastmoneyF10ReportSource

        src = EastmoneyF10ReportSource(client=_shared_http())
        try:
            return src.fetch_income_sheet(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            )
        finally:
            src.close()

    @staticmethod
    def cash_flow(
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """现金流量表明细（东财 F10，按报告期降序）。

        经营现金流与净利润的匹配度（现金流质量）、投资与筹资活动净额、
        自由现金流等分析入口。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "sale_cash_in","cash_recv_operate_in","cash_pay_operate_out",
        "cash_pay_operate_net","cash_operate_net","cash_invest_net",
        "cash_finance_net","cash_add","cash_begin","cash_end"}, ...]``
        （金额单位元、``float`` 或 ``None``）
        """
        from .fin_report import EastmoneyF10ReportSource

        src = EastmoneyF10ReportSource(client=_shared_http())
        try:
            return src.fetch_cash_flow(
                symbol,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                raw=raw,
            )
        finally:
            src.close()

    @staticmethod
    def fin_report(
        symbol: str,
        report: str = "balance_sheet",
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """通用 F10 报表直查（字段**原样透传**，单位以东财报表页为准）。

        ``report`` 可传别名（``balance_sheet`` / ``income_sheet`` / ``cash_flow``）
        或原始报表名（``RPT_F10_FINANCE_GBALANCE`` 等）；非别名视作原始 type，
        服务端会拒绝已下线报表（报「报表配置不存在」）。

        与 :meth:`dc_query`（datacenter-web 报表族）区别：本方法走东财 F10
        报表族（同为 datacenter-web 后端），个股过滤键为 ``SECUCODE``
        （``600519.SH``），非 ``SECURITY_CODE``（6 位纯代码）。
        """
        from .fin_report import EastmoneyF10ReportSource

        src = EastmoneyF10ReportSource(client=_shared_http())
        try:
            return src.fetch_report(
                symbol,
                report,
                report_date=report_date,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
            )
        finally:
            src.close()

    # -- 治理 --------------------------------------------------------------- #
    @staticmethod
    def executive_holds(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """董监高持股变动明细（按变动日期降序）。

        排查内部人减持节奏与高位套现——比「十大流通股东」快照更细。

        Returns
        -------
        ``[{"code","name","executive","position","change_date","change_shares",
        "change_price","change_ratio","hold_shares","hold_ratio","relationship",
        "market"}, ...]``
        """
        from .governance import EastmoneyExecutiveHoldSource

        src = EastmoneyExecutiveHoldSource(client=_shared_http())
        try:
            return src.fetch_executive_holds(symbol, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def shareholder_changes(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """股东增减持明细（大股东 / 机构股东，按公告日期降序）。

        ``change_shares`` 正=增持、负=减持。与 :meth:`holder_changes`
        （董监高增减持聚合口径）互补。

        Returns
        -------
        ``[{"code","name","holder","holder_type","change_date","end_date",
        "change_shares","change_ratio","change_price","hold_after",
        "hold_ratio_after","direction","market","start_date"}, ...]``
        """
        from .governance import EastmoneyShareholderChangeSource

        src = EastmoneyShareholderChangeSource(client=_shared_http())
        try:
            return src.fetch_shareholder_changes(symbol, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def org_profile(symbol: str) -> dict[str, Any] | None:
        """公司概况快照（法定代表人 / 董事长 / 主营 / 办公地址 / 员工数）。

        护城河与治理分析的结构化锚点——这些字段无法从行情或财务表推导。

        Returns
        -------
        ``{"code","name","chairman","legal_person","secretary","president",
        "founded_date","listing_date","reg_address","address","postcode",
        "tel","fax","official_site","email","main_business","business_scope",
        "employee_num","province","industry","org_summary","org_name",
        "org_name_en","actual_holder","account_firm","law_firm","reg_capital",
        "trade_market","independent_directors","security_type"} | None``
        """
        from .governance import EastmoneyOrgProfileSource

        src = EastmoneyOrgProfileSource(client=_shared_http())
        try:
            return src.fetch_org_profile(symbol)
        finally:
            src.close()

    @staticmethod
    def org_profiles(symbols: Sequence[str], *, size: int = 50) -> list[dict[str, Any]]:
        """公司概况批量（按代码去重，保留服务端返回顺序）。"""
        from .governance import EastmoneyOrgProfileSource

        src = EastmoneyOrgProfileSource(client=_shared_http())
        try:
            return src.fetch_org_profiles(list(symbols), size=size)
        finally:
            src.close()

    # -- 卖方一致预期 ------------------------------------------------------- #
    @staticmethod
    def rating_forecast(
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        sort_columns: str = "RATING_ORG_NUM",
    ) -> list[dict[str, Any]]:
        """券商评级与目标价（默认按覆盖机构数降序；``symbol`` 空 = 全市场最新）。

        「估值合理性」与「市场情绪」两维度的量化入口：评级分布、多年度 EPS
        预测、目标价区间、覆盖机构数。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀；空串表示全市场最新评级。
        sort_columns:
            排序键；常用 ``RATING_ORG_NUM``（覆盖机构数最多）。

        Returns
        -------
        ``[{"code","name","rating_org_num","rating_buy_num","rating_add_num",
        "rating_long_num","rating_neutral_num","rating_reduce_num",
        "rating_sale_num","year1","year2","year3","year4","year_mark1",
        "year_mark2","year_mark3","year_mark4","eps1","eps2","eps3","eps4",
        "target_price_max","target_price_min","industry_board",
        "concept_boards","region_board"}, ...]``
        """
        from .governance import EastmoneyRatingForecastSource

        src = EastmoneyRatingForecastSource(client=_shared_http())
        try:
            return src.fetch_rating_forecast(
                symbol, page=page, size=size, sort_columns=sort_columns
            )
        finally:
            src.close()

    @staticmethod
    def rating_consensus(symbol: str, *, page: int = 1, size: int = 20) -> dict[str, Any] | None:
        """一致预期聚合快照（EPS 均值 + 目标价区间均值 + 评级分布，本地计算）。

        Returns
        -------
        ``{"code","name","sample","target_price_min","target_price_max",
        "target_price_mean","predict_eps_mean","rating_org_num",
        "rating_dist":{"买入":36,"增持":9,"长期":45}} | None``
        """
        from .governance import EastmoneyRatingForecastSource

        src = EastmoneyRatingForecastSource(client=_shared_http())
        try:
            return src.fetch_rating_consensus(symbol, page=page, size=size)
        finally:
            src.close()
