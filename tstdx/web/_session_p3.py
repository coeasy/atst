# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（北向持股 / 十大股东 / 解禁股票 / 业绩预告）——P3 数据源扩展。

本模块承载 :class:`tstdx.web.session.WebQuoteSession` 的**资金流向与股东结构**方法：

* 北向持股（沪股通/深股通持仓明细，:mod:`tstdx.web.corporate`）
* 十大股东（全部股东，含非流通股；区别于 ``free_holders`` 仅流通股东）
* 解禁股票（按个股解禁明细；区别于 ``unlock`` 按批次解禁）
* 业绩预告旧版（含 FORECASTCONTENT 文本描述；区别于 ``forecast`` 新版结构化字段）

所有报表走东财 datacenter-web 后端（:class:`EastmoneyDataCenterSource`），
零新依赖、复用 ``dc_query`` 通用入口。

接口事实（2026-09-11 抓包验证，tstdx 自有实现）::

    # 北向持股
    RPT_MUTUAL_HOLD — 1152556 行
    列: SECURITY_INNER_CODE, SECURITY_CODE, SECURITY_NAME, TRADE_MARKET_CODE,
        FREE_SHARES_RATIO, MARKET, MUTUAL_TYPE, HOLD_DATE, HOLD_SHARES_RATIO,
        HOLD_SHARES, HOLDVOL, FHOLDVOL, PARTICIPANT_NUM, A_SHARES_RATIO,
        GREATEST_EUTIME, ADD_MARKET_CAP, HOLD_MARKET_CAP
    # MUTUAL_TYPE: "001"=沪股通, "003"=深股通, "002"=港股通（待确认）

    # 十大股东（全部股东）
    RPT_F10_EH_HOLDERS — 3255379 行
    列: SECUCODE, SECURITY_CODE, ORG_CODE, END_DATE, HOLDER_NAME, HOLD_NUM,
        HOLD_NUM_RATIO, HOLD_NUM_CHANGE, CHANGE_RATIO, HOLDER_CODE, IS_HOLDORG,
        SECURITY_TYPE_CODE, SECURITY_NAME_ABBR, HOLDER_RANK, HOLDER_STATE,
        HOLDER_MARKET_CAP, HOLDER_NEW, HOLD_RATIO_QOQ, IS_REPORT, HOLDER_STATEE,
        SHARES_TYPE, HOLDER_CODE_OLD, NEW_CHANGE_RATIO, HOLDER_STATE_NEW,
        TOTAL_SHARES_NUM

    # 解禁股票（按个股）
    RPT_LIFT_STOCK — 34542 行
    列: SECURITY_CODE, SECUCODE, TRADE_MARKET_CODE, ORG_CODE, SECURITY_NAME_ABBR,
        FREE_DATE, FREE_SHARES, NON_FREE_SHARES, ADD_LISTING_SHARES, CLOSE_PRICE,
        ADD_LISTSHARES_RATIO, ADD_LISTING_CAP, TOTAL_SHARES, CIRCLE_SHARES

    # 业绩预告（旧版，含文本描述）
    RPT_PUBLIC_OP_PREDICT — 110195 行
    列: SECURITY_CODE, SECURITY_NAME_ABBR, NOTICE_DATE, REPORTDATE, FORECASTL,
        FORECASTT, INCREASEL, INCREASET, FORECASTCONTENT, CHANGEREASONDSCRPT,
        FORECASTTYPE, YEAREARLIER, TRADE_MARKET, TRADE_MARKET_CODE,
        SECURITY_TYPE, SECURITY_TYPE_CODE, PUBLISHNAME, ORG_CODE, INCREASEJZ,
        FORECASTJZ, FORECASTQK, ISLATEST
"""

from __future__ import annotations

from typing import Any

from ._session_market import _shared_http

__all__ = ["P3SessionMixin"]


class P3SessionMixin:
    """北向持股 / 十大股东 / 解禁股票 / 业绩预告（旧版）。"""

    # -- 北向持股 ----------------------------------------------------------- #
    @staticmethod
    def northbound_hold(
        *,
        symbol: str = "",
        hold_date: str = "",
        mutual_type: str = "",
        size: int = 50,
        page: int = 1,
        sort_columns: str = "HOLD_DATE",
    ) -> list[dict[str, Any]]:
        """北向持股明细（沪股通/深股通持仓）。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` 或 ``sh600519``）；空串=全市场。
        hold_date:
            持仓日期（``2026-06-30``）；空串=不限。
        mutual_type:
            通道类型（``"001"``=沪股通 / ``"003"``=深股通）；空串=全部。
        sort_columns:
            排序键；常用 ``HOLD_DATE`` / ``HOLD_SHARES`` / ``HOLD_MARKET_CAP``。

        Returns
        -------
        ``[{"security_inner_code","security_code","security_name",
        "trade_market_code","free_shares_ratio","market","mutual_type",
        "hold_date","hold_shares_ratio","hold_shares","holdvol",
        "fholdvol","participant_num","a_shares_ratio","greatest_eutime",
        "add_market_cap","hold_market_cap"}, ...]``

        ``hold_shares`` 单位股，``hold_market_cap`` 单位元，
        ``hold_shares_ratio`` 持股比例（%），``participant_num`` 参与家数。
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if hold_date:
            filters.append(f'HOLD_DATE="{hold_date}"')
        if mutual_type:
            filters.append(f'MUTUAL_TYPE="{mutual_type}"')
        src = EastmoneyDataCenterSource(report="northbound_hold", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns=sort_columns,
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 十大股东（全部股东）----------------------------------------------- #
    @staticmethod
    def top_holders(
        *,
        symbol: str = "",
        end_date: str = "",
        size: int = 20,
        page: int = 1,
        sort_columns: str = "HOLDER_RANK",
        sort_types: str = "1",
    ) -> list[dict[str, Any]]:
        """十大股东（全部股东，含非流通股）。

        与 ``free_holders``（十大流通股东）的区别：本报表含全部股东类型，
        包括战略投资者、控股股东等非流通股份。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` 或 ``sh600519``）；空串=全市场。
        end_date:
            报告期（``2026-06-30``）；空串=最近一期。

        Returns
        -------
        ``[{"secucode","security_code","org_code","end_date","holder_name",
        "hold_num","hold_num_ratio","hold_num_change","change_ratio",
        "holder_code","is_holdorg","security_type_code","security_name_abbr",
        "holder_rank","holder_state","holder_market_cap","holder_new",
        "hold_ratio_qoq","is_report","holder_statee","shares_type",
        "holder_code_old","new_change_ratio","holder_state_new",
        "total_shares_num"}, ...]``

        ``hold_num`` 单位股，``hold_num_ratio`` 持股比例（%），
        ``holder_market_cap`` 持股市值（元）。
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            market, code = split_symbol(symbol)
            # 十大股东报表以 SECUCODE（600519.SH）过滤
            suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market, "SZ")
            secucode = f"{code}.{suffix}"
            filters.append(f'SECUCODE="{secucode}"')
        if end_date:
            filters.append(f'END_DATE="{end_date}"')
        src = EastmoneyDataCenterSource(report="top_holders", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 解禁股票（按个股）------------------------------------------------- #
    @staticmethod
    def unlock_stocks(
        *,
        symbol: str = "",
        begin: str = "",
        end: str = "",
        size: int = 50,
        page: int = 1,
        sort_columns: str = "FREE_DATE",
    ) -> list[dict[str, Any]]:
        """解禁股票明细（按个股维度）。

        与 ``unlock``（按批次解禁，RPT_LIFT_STAGE）的区别：本报表按个股汇总，
        含非流通/新增上市股份拆分。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` 或 ``sh600519``）；空串=全市场。
        begin / end:
            解禁日区间（``YYYY-MM-DD``）；为空表示不限。

        Returns
        -------
        ``[{"security_code","secucode","trade_market_code","org_code",
        "security_name_abbr","free_date","free_shares","non_free_shares",
        "add_listing_shares","close_price","add_listshares_ratio",
        "add_listing_cap","total_shares","circle_shares"}, ...]``

        ``free_shares`` / ``non_free_shares`` 单位万股，
        ``add_listing_cap`` 单位万元，``close_price`` 单位元。
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if begin:
            filters.append(f"FREE_DATE>='{begin}'")
        if end:
            filters.append(f"FREE_DATE<='{end}'")
        src = EastmoneyDataCenterSource(report="unlock_stocks", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns=sort_columns,
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 业绩预告（旧版，含文本描述）--------------------------------------- #
    @staticmethod
    def earnings_preview(
        *,
        symbol: str = "",
        report_date: str = "",
        size: int = 50,
        page: int = 1,
        sort_columns: str = "NOTICE_DATE",
    ) -> list[dict[str, Any]]:
        """业绩预告（旧版，含 FORECASTCONTENT 文本描述）。

        与 ``forecast``（新版 RPT_PUBLIC_OP_NEWPREDICT）的区别：
        本报表含 ``forecast_content``（自然语言描述）和
        ``change_reason_dscrpt``（变动原因），适合 NLP/情感分析。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` 或 ``sh600519``）；空串=全市场。
        report_date:
            报告期（``2026-06-30``）；空串=不限。

        Returns
        -------
        ``[{"security_code","security_name_abbr","notice_date","reportdate",
        "forecastl","forecastt","increasel","increaset","forecast_content",
        "change_reason_dscrpt","forecasttype","yearearlier","trade_market",
        "trade_market_code","security_type","security_type_code",
        "publishname","org_code","increasejz","forecastjz","forecastqk",
        "islatest"}, ...]``

        ``forecastl`` / ``forecastt`` 单位元（预告净利润下限/上限），
        ``increasel`` / ``increaset`` 单位 %（同比增幅下限/上限）。
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if report_date:
            rd = report_date if "00:00:00" in report_date else f"{report_date} 00:00:00"
            filters.append(f'REPORTDATE="{rd}"')
        src = EastmoneyDataCenterSource(report="earnings_preview", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns=sort_columns,
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()
