# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""天天基金扩展 Mixin：排行 / 快照 / 画像 / 经理 / 公司 / 搜索。

补齐 :class:`WebQuoteSession` 的基金域缺口（在
:mod:`tstdx.web.efinance_fund` 与 :mod:`tstdx.web.adapters_fund` 之外）：

模块划分
--------
* :class:`FundRankSessionMixin`：排行 / 批量快照 / 净值 / 详情 / 评级 /
  收益走势 / 排名走势（7 方法）
* :class:`FundManagerSessionMixin`：基金经理列表 / 档案 / 业绩走势 /
  业绩评价 / 风格画像（5 方法）
* :class:`FundCompanySessionMixin`：公司列表 / 概况 / 旗下基金 / 规模变动 /
  公司画像 / 基金搜索（6 方法）

.. note::
   全部为「仅 web」路由（天天基金后端），复用 :func:`_shared_http` 连接池。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._facade_mixin_market import _shared_http  # 共享连接池助手
from .fund_company import FundCompanySource
from .fund_manager import FundManagerSource
from .fund_rank import FundMobRankSource

__all__ = [
    "FundRankSessionMixin",
    "FundManagerSessionMixin",
    "FundCompanySessionMixin",
]


class FundRankSessionMixin:
    """基金排行 / 快照 / 画像能力。"""

    # -- 排行 ------------------------------------------------------------- #
    @staticmethod
    def fund_rank(
        *,
        fund_type: int = 0,
        sort_column: str = "SYL_1N",
        sort: str = "desc",
        page: int = 1,
        size: int = 20,
        company_id: str = "",
        topic: str = "",
        risk_level: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        """基金排行榜。返回 ``{"total","page","size","rows"}``。

        ``sort_column`` 见 :data:`tstdx.web.fund_rank.SORT_COLUMNS`
        （``SYL_1N`` 近1年 / ``SYL_1M`` 近1月 / ``SYL_Z`` 成立至今 /
        ``RDZF`` 日涨幅 等）；``company_id`` 来自 :meth:`fund_companies`。
        """
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_rank(
                fund_type=fund_type,
                sort_column=sort_column,
                sort=sort,
                page=page,
                size=size,
                company_id=company_id,
                topic=topic,
                risk_level=risk_level,
                **extra,
            )
        finally:
            src.close()

    # -- 批量实时快照（替代已下线的 fundgz 估值接口） ---------------------- #
    @staticmethod
    def fund_snapshot(codes: Sequence[str] | str) -> list[dict[str, Any]]:
        """批量基金实时快照（净值 + 盘中估值），一次请求多只。

        ``est_nav`` / ``est_pct`` / ``est_time`` 即估算净值 / 估算涨跌% /
        估算时间；``fund_estimate`` 依赖的 ``fundgz`` 接口已下线，本方法是
        官方替代路径。
        """
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_snapshot(codes)
        finally:
            src.close()

    # -- 移动端历史净值 --------------------------------------------------- #
    @staticmethod
    def fund_nav_history_mob(
        code: str, *, page: int = 1, size: int = 49
    ) -> list[dict[str, Any]]:
        """移动端历史净值（字段比 :meth:`fund_nav_history` 更全）。

        额外提供 ``nav_type``（净值类型）/ ``rate`` / ``cum_return``
        （累计收益率）。
        """
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_nav_history(code, page=page, size=size)
        finally:
            src.close()

    # -- 基金详情 --------------------------------------------------------- #
    @staticmethod
    def fund_detail(code: str) -> dict[str, Any]:
        """基金详情（风险等级 / 业绩基准 / 投资策略 / 各类费用）。

        选基尽调的核心字段：``risk_level`` / ``benchmark`` /
        ``invest_strategy`` / ``management_exp`` / ``trust_exp`` /
        ``sales_exp``。
        """
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_detail(code)
        finally:
            src.close()

    # -- 历史评级 --------------------------------------------------------- #
    @staticmethod
    def fund_rating(code: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """基金历史评级（天天基金 / 招商 / 上证 / 嘉实等机构）。"""
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_rating(code, page=page, size=size)
        finally:
            src.close()

    # -- 累计收益走势 ----------------------------------------------------- #
    @staticmethod
    def fund_yield_curve(
        code: str, *, index_code: str = "000300"
    ) -> list[dict[str, Any]]:
        """累计收益走势（基金 vs 指数 vs 同类），超额收益分析基础。

        ``index_code`` 可选 ``000300`` 沪深300 / ``000001`` 上证 /
        ``399001`` 深成 / ``399006`` 创业板 / ``000905`` 中证500。
        """
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_yield_curve(code, index_code=index_code)
        finally:
            src.close()

    # -- 同类排名走势 ----------------------------------------------------- #
    @staticmethod
    def fund_rank_trend(code: str, *, range_: str = "n") -> list[dict[str, Any]]:
        """同类排名走势（每日同类排名与总数）。"""
        src = FundMobRankSource(client=_shared_http())
        try:
            return src.fetch_rank_trend(code, range_=range_)
        finally:
            src.close()


class FundManagerSessionMixin:
    """基金经理能力（移动端 JSON，替代 ``fundf10`` HTML 解析）。"""

    @staticmethod
    def fund_manager_list(code: str) -> list[dict[str, Any]]:
        """基金经理列表（现任 + 离任）。

        稳定 JSON 版，替代 :meth:`fund_manager`（HTML best-effort 正则解析）。
        用 ``is_in_office`` 区分现任 / 离任。
        """
        src = FundManagerSource(client=_shared_http())
        try:
            return src.fetch_list(code)
        finally:
            src.close()

    @staticmethod
    def fund_manager_profile(mgrid: str) -> dict[str, Any]:
        """基金经理档案（简历 / 投资理念 / 任职基金 / 获奖）。

        ``mgrid`` 来自 :meth:`fund_manager_list` 的 ``mgrid`` 字段。
        """
        src = FundManagerSource(client=_shared_http())
        try:
            return src.fetch_profile(mgrid)
        finally:
            src.close()

    @staticmethod
    def fund_manager_yield(mgrid: str, *, range_: str = "y") -> list[dict[str, Any]]:
        """基金经理业绩走势（任职收益 vs 同类 vs 指数）。"""
        src = FundManagerSource(client=_shared_http())
        try:
            return src.fetch_yield(mgrid, range_=range_)
        finally:
            src.close()

    @staticmethod
    def fund_manager_eval(mgrid: str) -> dict[str, Any]:
        """基金经理业绩评价（夏普 / 最大回撤 / 胜率 / 波动率 / 超额）。

        ``sharp_1y`` / ``max_ret_1y`` / ``win_pct_1y`` / ``stddev_1y``
        可直接构成量化经理打分卡。
        """
        src = FundManagerSource(client=_shared_http())
        try:
            return src.fetch_eval(mgrid)
        finally:
            src.close()

    @staticmethod
    def fund_manager_style(mgrid: str) -> dict[str, Any]:
        """基金经理持仓风格画像（重仓股 / 风格标签 / 子风格分布）。"""
        src = FundManagerSource(client=_shared_http())
        try:
            return src.fetch_style(mgrid)
        finally:
            src.close()


class FundCompanySessionMixin:
    """基金公司 / 搜索能力。"""

    @staticmethod
    def fund_companies() -> list[dict[str, Any]]:
        """全部基金公司列表（约 160 家，一次拉全）。

        ``company_id`` 是后续 :meth:`fund_company_archives` /
        :meth:`fund_company_funds` / :meth:`fund_rank` 的过滤入参。
        """
        src = FundCompanySource(client=_shared_http())
        try:
            return src.fetch_companies()
        finally:
            src.close()

    @staticmethod
    def fund_company_archives(company_id: str) -> dict[str, Any]:
        """基金公司概况（成立时间 / 资产总规模 / 注册地 / 官网 / 人数）。"""
        src = FundCompanySource(client=_shared_http())
        try:
            return src.fetch_archives(company_id)
        finally:
            src.close()

    @staticmethod
    def fund_company_funds(
        company_id: str,
        *,
        fund_type: str = "all",
        page: int = 1,
        size: int = 50,
        sort_field: str = "DWJZ",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        """公司旗下基金列表（含各类区间收益，便于横向比较）。"""
        src = FundCompanySource(client=_shared_http())
        try:
            return src.fetch_funds(
                company_id,
                fund_type=fund_type,
                page=page,
                size=size,
                sort_field=sort_field,
                sort_dir=sort_dir,
            )
        finally:
            src.close()

    @staticmethod
    def fund_company_scale(
        company_id: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """公司旗下基金总规模变动（份额 / 净值资产，按报告期）。"""
        src = FundCompanySource(client=_shared_http())
        try:
            return src.fetch_scale_change(company_id, page=page, size=size)
        finally:
            src.close()

    @staticmethod
    def fund_company_base_info(company_id: str) -> dict[str, Any]:
        """公司画像（旗下基金分类统计 + 主题热度）。"""
        src = FundCompanySource(client=_shared_http())
        try:
            return src.fetch_base_info(company_id)
        finally:
            src.close()

    @staticmethod
    def fund_search(
        key: str, *, order_type: int = 2, page: int = 1, size: int = 10
    ) -> dict[str, Any]:
        """按名称 / 代码模糊搜索基金。返回 ``{"total","page","size","rows"}``。"""
        src = FundCompanySource(client=_shared_http())
        try:
            return src.search_funds(
                key, order_type=order_type, page=page, size=size
            )
        finally:
            src.close()
