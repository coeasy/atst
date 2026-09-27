# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（ESG 评级 / 筹码分布）——P14 数据源补全。

本模块承载 :class:`atst.web.session.WebQuoteSession` 的 **P1 扩展** 方法：

* ESG 评级（:mod:`atst.web.esg`，新浪 13 家机构 ESG 评级数据）
* 筹码分布（:mod:`atst.web.chip`，东财资金流驱动的筹码集中度分析）

补 :doc:`/docs/archive/parity/stock_analysis_prompt_coverage`（归档审计）中的 P1 缺口——
维度 9「ESG 表现」与维度 8「筹码分布」此前完全缺失。

与 :class:`FundamentalSessionMixin` 的组合关系：本 Mixin 依赖相同的
:func:`_shared_http` 连接池与 ``try / finally close()`` 生命周期约定，
但方法体独立（不同后端 / 不同解析结构），不做继承耦合。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._session_market import _shared_http

__all__ = ["P1SessionMixin"]


class P1SessionMixin:
    """ESG 评级 / 筹码分布（P1 扩展）。"""

    # -- ESG 评级 ----------------------------------------------------------- #
    @staticmethod
    def esg_rating(symbol: str) -> dict[str, Any] | None:
        """个股 ESG 评级详情（新浪 13 家机构聚合）。

        Returns
        -------
        ``{"agencies":[{"agency_name","esg_score","esg_level",
        "esg_dt","e_score","s_score","g_score"}, ...]} | None``
        """
        from .esg import SinaEsgStockInfoSource

        src = SinaEsgStockInfoSource(client=_shared_http())
        try:
            return src.fetch_stock_info(symbol)
        finally:
            src.close()

    @staticmethod
    def esg_history(symbol: str) -> dict[str, Any] | None:
        """个股 ESG 评级历史（季度变动，按机构分组）。

        Returns
        -------
        ``{"agencies":[{"agency_name","type","history":
        {"2025Q2":"61.9","2026Q1":"57.6",...}}]} | None``
        """
        from .esg import SinaEsgHistorySource

        src = SinaEsgHistorySource(client=_shared_http())
        try:
            return src.fetch_history(symbol)
        finally:
            src.close()

    @staticmethod
    def esg_ratings_all(
        source: str = "msci",
        *,
        market: str = "",
        rating: str = "",
        sort_column: str = "esg_rating",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """全市场 ESG 评级列表（支持 MSCI / 华证两种源）。

        Parameters
        ----------
        source:
            ``"msci"`` 或 ``"hz"``（华证）。
        market:
            市场过滤：MSCI 用 ``"CN"``/``"HK"``，华证用 ``"cn"``。
        rating / grade:
            评级过滤（MSCI: AAA/AA/A/BBB; 华证: AAA/AA/A）。
        """
        if source == "hz":
            from .esg import SinaEsgHzSource

            hz_source = SinaEsgHzSource(client=_shared_http())
            try:
                return hz_source.fetch_ratings(
                    market=market,
                    grade=rating,
                    sort_column=sort_column,
                    sort_order=sort_order,
                )
            finally:
                hz_source.close()
        else:
            from .esg import SinaEsgMsciSource

            msci_source = SinaEsgMsciSource(client=_shared_http())
            try:
                return msci_source.fetch_ratings(
                    market=market,
                    rating=rating,
                    sort_column=sort_column,
                    sort_order=sort_order,
                )
            finally:
                msci_source.close()

    # -- 筹码分布 ----------------------------------------------------------- #
    @staticmethod
    def chip_distribution(symbol: str, *, days: int = 5) -> dict[str, Any] | None:
        """个股筹码分布分析（资金流驱动，收集/发散判定）。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀（``600519`` / ``sh600519``）。
        days:
            分析天数（1-60，默认 5）。

        Returns
        -------
        ``{"symbol","name","period","total_amount","main_net_total",
        "main_net_ratio","super_large_ratio","accumulation_ratio",
        "concentration_trend","daily":[{...}, ...]} | None``
        """
        from .chip import EastmoneyChipDistributionSource

        src = EastmoneyChipDistributionSource(client=_shared_http())
        try:
            return src.fetch_chip_distribution(symbol, days=days)
        finally:
            src.close()

    @staticmethod
    def chip_distributions(
        symbols: Sequence[str], *, days: int = 5, max_count: int = 20
    ) -> list[dict[str, Any]]:
        """批量筹码分布分析（按 accumulation_ratio 降序排列）。"""
        from .chip import EastmoneyChipDistributionSource

        src = EastmoneyChipDistributionSource(client=_shared_http())
        try:
            return src.fetch_chip_distributions(symbols, days=days, max_count=max_count)
        finally:
            src.close()
