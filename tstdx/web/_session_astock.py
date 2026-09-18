# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""astock-data-toolkit 对标扩展 Mixin（估值 / 分红 / 增减持 / 财务摘要 / 公告）。

收口 https://github.com/tiantianlaolao/astock-data-toolkit 数据域中 tstdx 此前
缺失的基本面衍生接口，作为 :class:`WebQuoteSession` 的域 Mixin 组合。各方法均为
「仅 web」路由（东财 datacenter / 公告后端）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._session_market import _shared_http  # 共享连接池助手
from .astock_toolkit import (
    EastmoneyAnnouncementSource,
    EastmoneyDividendSource,
    EastmoneyFinanceMainSource,
    EastmoneyHolderChangeSource,
    EastmoneyValuationSource,
)

__all__ = ["AstockToolkitMixin"]


class AstockToolkitMixin:
    """astock-data-toolkit 对标补全（基本面衍生数据接口）。"""

    # -- 分红送转 -------------------------------------------------------- #
    @staticmethod
    def dividend_history(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """分红送转历史（对标工具箱 ``dividend_history`` Parquet 表）。

        东财 ``RPT_SHAREBONUS_DET``（2026-09-06 实测可用）。比例字段为
        「每 10 股」口径（与东财报表一致）：``bonus_shares_per_10`` 送股、
        ``transfer_shares_per_10`` 转增、``cash_dividend_per_10`` 派息（元）。
        """
        src = EastmoneyDividendSource(client=_shared_http())
        try:
            return src.fetch_dividend(symbol, page=page, size=size)
        finally:
            src.close()

    # -- 估值分析 -------------------------------------------------------- #
    @staticmethod
    def stock_valuation(symbol: str) -> list[dict[str, Any]]:
        """个股估值快照（对标工具箱 ``valuation_daily``，东财聚合版）。

        返回按报告期降序的 ``pe_ttm / pb / ps_ttm / pcf_ttm / total_share /
        total_mv``。工具箱原版为 baostock+巨潮日频序列，此处为东财报表快照
        （季度粒度），取首项即「最新估值」。

        .. note::
           报表名 ``RPT_VALUEASSESS_DET`` 为 best-effort，若服务端返回
           ``code=9501`` 需重新抓包校准。
        """
        src = EastmoneyValuationSource(client=_shared_http())
        try:
            return src.fetch_valuation(symbol)
        finally:
            src.close()

    # -- 股东增减持 ------------------------------------------------------ #
    @staticmethod
    def holder_changes(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """股东 / 董监高增减持（对标工具箱 ``holder_changes`` 三所披露源）。

        东财聚合接口 ``RPT_CAPITAL_PARTICIPATION_DET``（单入口、免反爬）。
        ``change_shares`` 正=增持、负=减持。

        .. note::
           报表名 best-effort，若返回 ``code=9501`` 需重新抓包校准。
        """
        src = EastmoneyHolderChangeSource(client=_shared_http())
        try:
            return src.fetch_holder_changes(symbol, page=page, size=size)
        finally:
            src.close()

    # -- 财务摘要 / 主要指标 --------------------------------------------- #
    @staticmethod
    def financial_abstract(symbol: str) -> list[dict[str, Any]]:
        """财务主要指标摘要（对标工具箱「财务摘要 28 指标」，东财原生精简版）。

        返回 ``eps / roe / bps / revenue / net_profit / 同比 / 毛利率 /
        资产负债率`` 等核心指标，按报告期降序。

        .. note::
           报表名 ``RPT_F10_FINANCE_MAIN`` 为 best-effort，若返回
           ``code=9501`` 需重新抓包校准。
        """
        src = EastmoneyFinanceMainSource(client=_shared_http())
        try:
            return src.fetch_main_indicators(symbol)
        finally:
            src.close()

    # -- 上市公司公告 ---------------------------------------------------- #
    @staticmethod
    def announcements(
        symbols: Sequence[str], *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """上市公司公告列表（对标工具箱 ``info_archive.db`` 公告表）。

        复用 :class:`EastmoneyNoticeSource`（东财 ``np-anotice-stock``）。
        ``symbols`` 可传多只；返回
        ``[{"art_code","title","notice_date","display_time","categories",
        "codes"}, ...]``。
        """
        src = EastmoneyAnnouncementSource(client=_shared_http())
        try:
            return src.fetch_notices(list(symbols), page=page, size=size)
        finally:
            src.close()
