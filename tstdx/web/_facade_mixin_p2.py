# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（行业指数 / 概念指数 / 宏观经济 / 可转债）——P2 数据源扩展。

本模块承载 :class:`tstdx.web.facade.WebQuoteSession` 的**行业与宏观**方法：

* 行业指数（板块/概念指标：涨跌幅/排名，:mod:`tstdx.web.corporate`）
* 概念指数成分（股票代码→概念映射）
* 宏观经济（CPI / PPI / GDP）
* 可转债列表（基本信息 / 到期日 / 转股价 / 评级）

所有报表走东财 datacenter-web 后端（:class:`EastmoneyDataCenterSource`），
零新依赖、复用 ``dc_query`` 通用入口。

接口事实（2026-09-11 抓包验证，tstdx 自有实现）::

    # 行业指数（板块/概念指标）
    RPT_INDUSTRY_INDEX — 177485 行
    列: REPORT_DATE, INDICATOR_VALUE, CHANGE_RATE, CHANGERATE_3M/6M/1Y/2Y/3Y,
        BOARD_CODE, BOARD_NAME, CONCEPT_CODE, CONCEPT_NAME,
        INDICATOR_ID, INDICATOR_NAME, RANK_LABEL

    # 概念指数成分
    RPT_CONCEPT_INDEX — 77431 行
    列: SECURITY_CODE, INDEX_CODE, INDEX_NAME_ABBR, INDEX_NAME

    # 宏观经济 CPI
    RPT_ECONOMY_CPI — 224 行
    列: REPORT_DATE, TIME, NATIONAL_SAME, NATIONAL_BASE, NATIONAL_SEQUENTIAL,
        NATIONAL_ACCUMULATE, CITY_SAME, CITY_BASE, ..., RURAL_*

    # 宏观经济 PPI
    RPT_ECONOMY_PPI — 248 行
    列: REPORT_DATE, TIME, BASE, BASE_SAME, BASE_ACCUMULATE

    # 宏观经济 GDP
    RPT_ECONOMY_GDP — 82 行
    列: REPORT_DATE, TIME, DOMESTICL_PRODUCT_BASE, FIRST_PRODUCT_BASE,
        SECOND_PRODUCT_BASE, THIRD_PRODUCT_BASE, SUM_SAME, FIRST_SAME, ...

    # 可转债列表
    RPT_BOND_CB_LIST — 1052 行
    列: SECURITY_CODE, SECUCODE, SECURITY_NAME_ABBR, LISTING_DATE, BOND_EXPIRE,
        RATING, CONVERT_STOCK_CODE, ACTUAL_ISSUE_SCALE, ISSUE_PRICE, PAR_VALUE, ...
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._facade_mixin_market import _shared_http

__all__ = ["P2SessionMixin"]


class P2SessionMixin:
    """行业指数 / 概念指数 / 宏观经济 / 可转债。"""

    # -- 行业指数 ----------------------------------------------------------- #
    @staticmethod
    def industry_index(
        *,
        report_date: str = "",
        board_code: str = "",
        size: int = 20,
        page: int = 1,
        sort_columns: str = "CHANGE_RATE",
    ) -> list[dict[str, Any]]:
        """行业指数指标（板块/概念，含涨跌幅/多周期涨跌/排名）。

        Parameters
        ----------
        report_date:
            报告期（``2026-09-10``）；空串=最新。
        board_code:
            板块代码（如 ``901001``）；空串=全行业。
        sort_columns:
            排序键；常用 ``CHANGE_RATE``（涨幅）/ ``RANK_LABEL``（排名）。

        Returns
        -------
        ``[{"report_date","indicator_value","change_rate","change_rate_3m",
        "change_rate_6m","change_rate_1y","change_rate_2y","change_rate_3y",
        "is_newest","board_code","board_name","concept_code","concept_name",
        "indicator_id","indicator_name","rank_label"}, ...]``
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if report_date:
            filters.append(f'REPORT_DATE="{report_date}"')
        if board_code:
            filters.append(f'BOARD_CODE="{board_code}"')
        src = EastmoneyDataCenterSource(report="industry_index", client=_shared_http())
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

    # -- 概念指数成分 ------------------------------------------------------- #
    @staticmethod
    def concept_index(
        *,
        index_code: str = "",
        size: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """概念指数成分（股票代码 → 概念映射）。

        Parameters
        ----------
        index_code:
            概念代码（如 ``BK1027``）；空串=全概念。

        Returns
        -------
        ``[{"security_code","index_code","index_name_abbr","index_name"}, ...]``
        """
        from .corporate import EastmoneyDataCenterSource

        filters: list[str] = []
        if index_code:
            filters.append(f'INDEX_CODE="{index_code}"')
        src = EastmoneyDataCenterSource(report="concept_index", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns="INDEX_CODE",
                sort_types="1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 宏观经济: CPI ------------------------------------------------------ #
    @staticmethod
    def macro_cpi(
        *,
        size: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """CPI 宏观数据（全国/城镇/农村，含同比/环比/累计）。

        Returns
        -------
        ``[{"report_date","time","national_same","national_base",
        "national_sequential","national_accumulate","city_same","city_base",
        "city_sequential","city_accumulate","rural_same","rural_base",
        "rural_sequential","rural_accumulate"}, ...]``
        （百分比值，``float`` 或 ``None``）
        """
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report="macro_cpi", client=_shared_http())
        try:
            return src.fetch_rows(
                sort_columns="REPORT_DATE",
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 宏观经济: PPI ------------------------------------------------------ #
    @staticmethod
    def macro_ppi(
        *,
        size: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """PPI 宏观数据（出厂价同比/环比/累计）。

        Returns
        -------
        ``[{"report_date","time","base","base_same","base_accumulate"}, ...]``
        （百分比值，``float`` 或 ``None``）
        """
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report="macro_ppi", client=_shared_http())
        try:
            return src.fetch_rows(
                sort_columns="REPORT_DATE",
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 宏观经济: GDP ------------------------------------------------------ #
    @staticmethod
    def macro_gdp(
        *,
        size: int = 50,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """GDP 宏观数据（GDP 总量 / 三产占比 / 同比增速）。

        Returns
        -------
        ``[{"report_date","time","domestic_product_base",
        "first_product_base","second_product_base","third_product_base",
        "sum_same","first_same","second_same","third_same"}, ...]``
        （GDP 单位亿元，增速百分比，``float`` 或 ``None``）
        """
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report="macro_gdp", client=_shared_http())
        try:
            return src.fetch_rows(
                sort_columns="REPORT_DATE",
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()

    # -- 可转债列表 --------------------------------------------------------- #
    @staticmethod
    def convertible_bonds(
        *,
        size: int = 50,
        page: int = 1,
        sort_columns: str = "LISTING_DATE",
    ) -> list[dict[str, Any]]:
        """可转债列表（基本信息 / 到期日 / 转股价 / 评级）。

        Returns
        -------
        ``[{"security_code","secucode","security_name_abbr","trade_market",
        "listing_date","bond_expire","rating","convert_stock_code",
        "actual_issue_scale","issue_price","par_value","redeem_type",
        "interest_rate_explain","expire_date","delist_date", ...}, ...]``
        （规模单位元，价格单位元，``float`` 或 ``None``）
        """
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report="convertible_bonds", client=_shared_http())
        try:
            return src.fetch_rows(
                sort_columns=sort_columns,
                sort_types="-1",
                page=page,
                size=size,
            )
        finally:
            src.close()
