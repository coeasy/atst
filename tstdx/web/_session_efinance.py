# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""efinance 对标扩展 Mixin（基金 / 期货 / 债券 / 股票扩展能力）。

收口 ``efinance`` 系列尚未被 tstdx 覆盖的公开能力，作为 :class:`WebQuoteSession`
的域 Mixin 组合。各方法均为「仅 web」路由（东财 / 天天基金后端）。

模块划分
--------
* :class:`StockEfinanceMixin`：股票扩展（基础资料 / 全市场业绩 / 报告期 / IPO 审核）
* :class:`FundMobSessionMixin`：基金扩展（基金经理 / 持仓 / 阶段涨幅 / 资产配置 / 行业分布 / 公开日期）
* :class:`DerivativeSessionMixin`：期货 / 债券 / 期权（实时 / K 线 / 基础信息 / 资金流 / 成交明细）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._session_market import _shared_http  # 共享连接池助手
from .corporate import (
    VALID_REPORTS,
    EastmoneyDataCenterSource,
    EastmoneyPerformanceSource,
    EastmoneyProfileSource,
)
from .efinance_deriv import EastmoneyBondSource, EastmoneyFuturesSource
from .efinance_fund import FundMobSource
from .efinance_options import EastmoneyOptionsSource

__all__ = [
    "StockEfinanceMixin",
    "FundMobSessionMixin",
    "DerivativeSessionMixin",
]


class StockEfinanceMixin:
    """股票扩展能力（efinance.stock 对标补全）。"""

    # -- 基础资料（批量） ------------------------------------------------ #
    @staticmethod
    def stock_base_info(codes: Sequence[str]) -> list[dict[str, Any]]:
        """批量股票基础资料（市盈率 / 市净率 / 行业 / 总市值 / 流通市值）。

        对标 efinance ``get_base_info``（单 / 多只）。返回
        ``[{"code","name","price","pe_dynamic","pb","industry","board",
        "total_market_cap","float_market_cap","total_shares","float_shares"}, ...]``
        （金额单位元，PE/PB 经 ×100 还原）。
        """
        out: list[dict[str, Any]] = []
        src = EastmoneyProfileSource(client=_shared_http())
        try:
            for code in codes:
                try:
                    out.append(src.fetch_profile(code))
                except Exception:  # noqa: BLE001 单只失败不阻断批量
                    continue
        finally:
            src.close()
        return out

    # -- 全市场业绩 ------------------------------------------------------ #
    @staticmethod
    def stock_all_performance(report_date: str = "") -> list[dict[str, Any]]:
        """全市场定期报告业绩（对标 efinance ``get_all_company_performance``）。

        ``report_date`` 为空取最新一期；否则按报告期过滤（如 ``2026-06-30``）。
        """
        src = EastmoneyPerformanceSource(client=_shared_http())
        try:
            return src.fetch_performance(report_date=report_date, all_pages=True)
        finally:
            src.close()

    # -- 报告期列表 ------------------------------------------------------ #
    @staticmethod
    def stock_report_dates(limit: int = 100) -> list[str]:
        """全市场财报报告期列表（去重，降序；对标 efinance ``get_all_report_dates`` 简版）。

        取业绩报表的 ``REPORTDATE`` 列去重，作为「有哪些报告期」索引。
        """
        src = EastmoneyDataCenterSource(client=_shared_http())
        try:
            rows = src.fetch_rows(
                sort_columns="REPORTDATE",
                sort_types="-1",
                page=1,
                size=min(max(1, limit), 500),
                report=VALID_REPORTS["performance"],
            )
        finally:
            src.close()
        dates = sorted({str(r["REPORTDATE"]) for r in rows if r.get("REPORTDATE")}, reverse=True)
        return dates

    # -- IPO 审核状态 ---------------------------------------------------- #
    @staticmethod
    def ipo_review(*, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """IPO 审核状态（发行人 / 保荐机构 / 审核状态 / 更新日期）。

        对标 efinance ``get_latest_ipo_info``（与 :meth:`ipo_calendar` 申购日历
        不同，本方法聚焦「审核进度」）。

        .. note::
           报表名 ``RPT_IPO_AUDIT`` 为 best-effort（东财审核报表名偶发变动），
           若服务端返回「报表配置不存在」需重新抓包校准。解析层对通用字段做
           容错映射。
        """
        src = EastmoneyDataCenterSource(report="RPT_IPO_AUDIT", client=_shared_http())
        try:
            rows = src.fetch_rows(page=page, size=size, sort_columns="UPDATE_DATE", sort_types="-1")
        finally:
            src.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "code": str(r.get("SECURITY_CODE", "")),
                    "name": str(r.get("SECURITY_NAME", "") or r.get("NAME", "")),
                    "issuer": str(r.get("ORG_NAME", "") or r.get("ISSUER", "")),
                    "sponsor": str(r.get("SPONSOR_NAME", "") or r.get("SPONSOR", "")),
                    "audit_state": str(r.get("AUDIT_STATE", "") or r.get("STATE", "")),
                    "update_date": str(r.get("UPDATE_DATE", "") or r.get("NOTICE_DATE", "")),
                }
            )
        return out


class FundMobSessionMixin:
    """基金扩展能力（efinance.fund 对标补全，天天基金移动端后端）。"""

    @staticmethod
    def fund_base_info(code: str) -> dict[str, Any]:
        """基金基础信息（对标 efinance ``get_base_info`` 单数）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_base_info(code)
        finally:
            src.close()

    @staticmethod
    def fund_base_info_multi(codes: Sequence[str]) -> list[dict[str, Any]]:
        """批量基金基础信息（对标 efinance ``get_base_info_muliti``）。

        逐只调用 ``fetch_base_info``，单只失败不阻断批量（与
        :meth:`StockEfinanceMixin.stock_base_info` 一致）。
        """
        out: list[dict[str, Any]] = []
        src = FundMobSource(client=_shared_http())
        try:
            for code in codes:
                try:
                    out.append(src.fetch_base_info(code))
                except Exception:  # noqa: BLE001 单只失败不阻断批量
                    continue
        finally:
            src.close()
        return out

    @staticmethod
    def fund_manager(code: str) -> dict[str, Any] | None:
        """基金经理（对标 efinance ``get_fund_manager``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_manager(code)
        finally:
            src.close()

    @staticmethod
    def fund_holdings(code: str, dates: Sequence[str] | str | None = None) -> list[dict[str, Any]]:
        """基金持仓（对标 efinance ``get_invest_position``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_holdings(code, dates=dates)
        finally:
            src.close()

    @staticmethod
    def fund_period_change(code: str) -> list[dict[str, Any]]:
        """基金阶段涨幅（对标 efinance ``get_period_change``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_period_change(code)
        finally:
            src.close()

    @staticmethod
    def fund_asset_allocation(
        code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金资产配置（股票/债券/现金占比，对标 efinance ``get_types_percentage``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_asset_allocation(code, dates=dates)
        finally:
            src.close()

    @staticmethod
    def fund_industry_distribution(
        code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金行业分布（对标 efinance ``get_industry_distribution``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_industry_distribution(code, dates=dates)
        finally:
            src.close()

    @staticmethod
    def fund_public_dates(code: str) -> list[str]:
        """基金公开持仓日期列表（对标 efinance ``get_public_dates``）。"""
        src = FundMobSource(client=_shared_http())
        try:
            return src.fetch_public_dates(code)
        finally:
            src.close()


class DerivativeSessionMixin:
    """期货 / 债券 / 期权能力（efinance.futures / efinance.bond 对标，东财 push2 后端）。"""

    # -- 期货 ------------------------------------------------------------- #
    @staticmethod
    def futures_base_info() -> list[dict[str, Any]]:
        """全市场期货基础信息（对标 efinance ``futures.get_futures_base_info``）。"""
        src = EastmoneyFuturesSource(client=_shared_http())
        try:
            return src.fetch_base_info()
        finally:
            src.close()

    @staticmethod
    def futures_realtime(quote_id: str) -> dict[str, Any]:
        """期货实时快照（对标 efinance ``futures.get_realtime_quotes`` 单只）。"""
        src = EastmoneyFuturesSource(client=_shared_http())
        try:
            return src.fetch_realtime(quote_id)
        finally:
            src.close()

    @staticmethod
    def futures_kline(
        quote_id: str, *, period: str = "day", count: int = 320, adjust: str = ""
    ) -> list[Any]:
        """期货历史 K 线（对标 efinance ``futures.get_quote_history``）。"""
        src = EastmoneyFuturesSource(client=_shared_http())
        try:
            return src.fetch_kline(quote_id, period=period, count=count, adjust=adjust)
        finally:
            src.close()

    @staticmethod
    def futures_trades(quote_id: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """期货当日成交明细（对标 efinance ``futures.get_deal_detail``）。"""
        src = EastmoneyFuturesSource(client=_shared_http())
        try:
            return src.fetch_deal_detail(quote_id, max_count=max_count)
        finally:
            src.close()

    # -- 期权 ------------------------------------------------------------- #
    @staticmethod
    def options_list(
        *,
        market: str = "",
        size: int = 200,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """期权合约列表（按市场段筛选）。

        Parameters
        ----------
        market:
            市场段（``"10"`` 上证50ETF / ``"11"`` 沪深300股指 /
            ``"12"`` 深证100ETF）；空串=全部。
        """
        src = EastmoneyOptionsSource(client=_shared_http())
        try:
            return src.fetch_contract_list(market=market, size=size, page=page)
        finally:
            src.close()

    @staticmethod
    def options_snapshot(quote_id: str) -> dict[str, Any]:
        """单只期权合约快照（含认购/认沽方向与扩展字段）。"""
        src = EastmoneyOptionsSource(client=_shared_http())
        try:
            return src.fetch_snapshot(quote_id)
        finally:
            src.close()

    @staticmethod
    def options_trends(quote_id: str, *, ndays: int = 1) -> list[dict[str, Any]]:
        """期权当日分时走势（部分市场段可用，不可用时返回空列表）。"""
        src = EastmoneyOptionsSource(client=_shared_http())
        try:
            return src.fetch_trends(quote_id, ndays=ndays)
        finally:
            src.close()

    # -- 债券 ------------------------------------------------------------- #
    @staticmethod
    def bond_realtime(codes: Sequence[str]) -> list[dict[str, Any]]:
        """债券实时行情（对标 efinance ``bond.get_realtime_quotes``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_realtime(codes)
        finally:
            src.close()

    @staticmethod
    def bond_base_info(codes: Sequence[str]) -> list[dict[str, Any]]:
        """债券基础信息（对标 efinance ``bond.get_base_info``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_base_info(codes)
        finally:
            src.close()

    @staticmethod
    def bond_all_base_info() -> list[dict[str, Any]]:
        """全市场债券基础信息（对标 efinance ``bond.get_all_base_info``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_all_base_info()
        finally:
            src.close()

    @staticmethod
    def bond_kline(
        code: str, *, period: str = "day", count: int = 320, adjust: str = ""
    ) -> list[Any]:
        """债券历史 K 线（对标 efinance ``bond.get_quote_history``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_kline(code, period=period, count=count, adjust=adjust)
        finally:
            src.close()

    @staticmethod
    def bond_history_bill(code: str, *, count: int = 10) -> list[dict[str, Any]]:
        """债券历史资金流（对标 efinance ``bond.get_history_bill``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_history_bill(code, count=count)
        finally:
            src.close()

    @staticmethod
    def bond_today_bill(code: str) -> dict[str, Any] | None:
        """债券当日资金流（对标 efinance ``bond.get_today_bill``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_today_bill(code)
        finally:
            src.close()

    @staticmethod
    def bond_trades(code: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """债券当日成交明细（对标 efinance ``bond.get_deal_detail``）。"""
        src = EastmoneyBondSource(client=_shared_http())
        try:
            return src.fetch_deal_detail(code, max_count=max_count)
        finally:
            src.close()
