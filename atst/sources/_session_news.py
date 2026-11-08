# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""资讯类扩展 Mixin（财经快讯 / 研报 / 机构调研）。

收口 niuniu 审计中识别出的 3 个资讯类数据域缺口，作为 :class:`WebQuoteSession`
的域 Mixin 组合。各方法均为「仅 web」路由（东方财富后端）。

模块划分
--------
* :class:`NewsSessionMixin`：财经快讯（全市场头条）/ 个股研报 / 机构调研纪要
"""

from __future__ import annotations

from typing import Any

from ._session_market import _shared_http  # 共享连接池助手
from .corporate import EastmoneyResearchSource
from .news import EastmoneyNewsSource, EastmoneyResearchVisitSource

__all__ = ["NewsSessionMixin"]


class NewsSessionMixin:
    """资讯类扩展能力（对标 niuniu ``/api/news/*`` 三端点）。"""

    # -- 财经快讯（全市场滚动头条） ---------------------------------------- #
    @staticmethod
    def news_financial(*, page: int = 1, size: int = 30) -> list[dict[str, Any]]:
        """财经快讯头条（对标 niuniu ``/api/news/financial`` 新浪财经头条）。

        返回 ``[{"id","title","content","summary","time","url","labels"}, ...]``。
        """
        src = EastmoneyNewsSource(client=_shared_http())
        try:
            return src.fetch_news(page=page, size=size)
        finally:
            src.close()

    # -- 个股研报（评级 / 盈利预测） --------------------------------------- #
    @staticmethod
    def research_reports(
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        begin: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """个股研报（对标 niuniu ``/api/news/research/{code}`` 同花顺研报）。

        ``symbol`` 为空表示全市场最新研报。返回
        ``[{"info_code","title","stock_code","stock_name","org","researcher",
        "publish_date","rating","industry","eps_this_year","pe_this_year",
        "eps_next_year","pe_next_year"}, ...]``。
        """
        src = EastmoneyResearchSource(client=_shared_http())
        try:
            return src.fetch_reports(symbol, page=page, size=size, begin=begin, end=end)
        finally:
            src.close()

    # -- 机构调研（调研纪要） ---------------------------------------------- #
    @staticmethod
    def research_visits(symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """机构调研记录（对标 niuniu ``/api/news/research-visits/{code}`` 巨潮调研）。

        返回 ``[{"code","name","date","org","type","summary","content"}, ...]``。
        """
        src = EastmoneyResearchVisitSource(client=_shared_http())
        try:
            return src.fetch_visits(symbol, page=page, size=size)
        finally:
            src.close()
