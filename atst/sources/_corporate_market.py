# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""东财股东 / 大宗 / 解禁 / 业绩 / 预告 / IPO 适配器（v2/W7 从 corporate.py 拆分）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._corporate_common import VALID_REPORTS, _s
from ._corporate_financial import EastmoneyDataCenterSource
from .base import num_f as _f
from .base import num_i as _i


class EastmoneyShareholderSource(EastmoneyDataCenterSource):
    """十大流通股东与股东户数。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("free_holders", **kwargs)

    def fetch_free_holders(
        self,
        symbol: str,
        *,
        page: int = 1,
        size: int = 20,
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """十大流通股东。

        Parameters
        ----------
        end_date:
            报告期（``2026-06-30``）；空串表示取最近一期。

        Returns
        -------
        ``[{"holder_name","holder_rank","hold_num","hold_ratio",
        "change","holder_type","shares_type","report_date"}, ...]``
        """
        from ..domain.symbol import split_symbol

        # 十大股东报表以 SECUCODE（600519.SH）过滤；北交所后缀为 .BJ（P1 #14）
        market, code = split_symbol(symbol)
        suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market, "SZ")
        secucode = f"{code}.{suffix}"
        filters = [f'SECUCODE="{secucode}"']
        if end_date:
            filters.append(f'END_DATE="{end_date}"')
        rows = self.fetch_rows(
            filters=filters, sort_columns="HOLDER_RANK", sort_types="1", page=page, size=size
        )
        return [
            {
                "holder_name": _s(r.get("HOLDER_NAME")),
                "holder_rank": _i(r.get("HOLDER_RANK")),
                "hold_num": _f(r.get("HOLD_NUM")),
                "hold_ratio": _f(r.get("HOLD_RATIO")),
                "change": _s(r.get("HOLD_NUM_CHANGE")),
                "holder_type": _s(r.get("HOLDER_TYPE")),
                "shares_type": _s(r.get("SHARES_TYPE")),
                "report_date": _s(r.get("END_DATE")),
            }
            for r in rows
        ]

    def fetch_holder_num(
        self,
        symbol: str,
        *,
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股东户数变动历史（v5 PG3：默认翻页取全量历史）。

        Returns
        -------
        ``[{"holder_num","prev_holder_num","change","change_ratio",
        "end_date","avg_market_cap","avg_hold_num","notice_date"}, ...]``
        """
        # 报表名经参数传递（不再运行时临时替换 self.report——线程不安全，P1 #14）
        rows = self.fetch_rows_by_code(
            symbol,
            sort_columns="HOLD_NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            report=VALID_REPORTS["holder_num"],
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "holder_num": _i(r.get("HOLDER_NUM")),
                "prev_holder_num": _i(r.get("PRE_HOLDER_NUM")),
                "change": _i(r.get("HOLDER_NUM_CHANGE")),
                "change_ratio": _f(r.get("HOLDER_NUM_RATIO")),
                "end_date": _s(r.get("END_DATE")),
                "avg_market_cap": _f(r.get("AVG_MARKET_CAP")),
                "avg_hold_num": _f(r.get("AVG_HOLD_NUM")),
                "notice_date": _s(r.get("HOLD_NOTICE_DATE")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 大宗交易
# --------------------------------------------------------------------------- #
class EastmoneyBlockTradeSource(EastmoneyDataCenterSource):
    """大宗交易明细。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("block_trade", **kwargs)

    def fetch_block_trades(
        self,
        *,
        symbol: str = "",
        date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """大宗交易列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        symbol:
            指定证券；空串表示全市场。
        date:
            交易日（``2026-09-01``）；空串表示最近交易日。

        Returns
        -------
        ``[{"code","name","date","price","close_price","premium_ratio",
        "volume","amount","buyer","seller","turnover_rate"}, ...]``
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if date:
            filters.append(f'TRADE_DATE="{date}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "date": _s(r.get("TRADE_DATE")),
                "price": _f(r.get("DEAL_PRICE")),
                "close_price": _f(r.get("CLOSE_PRICE")),
                "premium_ratio": _f(r.get("PREMIUM_RATIO")),
                "volume": _f(r.get("DEAL_VOLUME")),
                "amount": _f(r.get("DEAL_AMT")),
                "buyer": _s(r.get("BUYER_NAME")),
                "seller": _s(r.get("SELLER_NAME")),
                "turnover_rate": _f(r.get("TURNOVER_RATE")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 限售股解禁
# --------------------------------------------------------------------------- #
class EastmoneyUnlockSource(EastmoneyDataCenterSource):
    """限售股解禁日程。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("unlock", **kwargs)

    def fetch_unlocks(
        self,
        *,
        symbol: str = "",
        begin: str = "",
        end: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """解禁批次列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        begin / end:
            解禁日区间（``YYYY-MM-DD``）；为空表示不限。

        Returns
        -------
        ``[{"code","name","free_date","free_shares","current_free_shares",
        "market_cap","shares_type","batch_holder_num"}, ...]``

        数量单位为**万股**，市值单位为**万元**（接口原始口径，保留不换算
        以便与东财页面对照）。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if begin:
            filters.append(f"FREE_DATE>='{begin}'")
        if end:
            filters.append(f"FREE_DATE<='{end}'")
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="FREE_DATE",
            sort_types="1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "free_date": _s(r.get("FREE_DATE")),
                "free_shares": _f(r.get("FREE_SHARES")),
                "current_free_shares": _f(r.get("CURRENT_FREE_SHARES")),
                "market_cap": _f(r.get("LIFT_MARKET_CAP")),
                "shares_type": _s(r.get("FREE_SHARES_TYPE")),
                "batch_holder_num": _i(r.get("BATCH_HOLDER_NUM")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 业绩报表
# --------------------------------------------------------------------------- #
class EastmoneyPerformanceSource(EastmoneyDataCenterSource):
    """定期报告业绩指标。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("performance", **kwargs)

    def fetch_performance(
        self,
        *,
        symbol: str = "",
        report_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """业绩报表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        report_date:
            报告期（``2026-06-30``）；空串表示取最近一期。

        Returns
        -------
        ``[{"code","name","report_date","notice_date","eps",
        "deduct_eps","revenue","net_profit","roe","gross_margin",
        "bps","operate_cashflow_ps","yoy_revenue","yoy_net_profit"}, ...]``
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if report_date:
            filters.append(f'REPORTDATE="{report_date}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "report_date": _s(r.get("REPORTDATE")),
                "notice_date": _s(r.get("NOTICE_DATE")),
                "eps": _f(r.get("BASIC_EPS")),
                "deduct_eps": _f(r.get("DEDUCT_BASIC_EPS")),
                "revenue": _f(r.get("TOTAL_OPERATE_INCOME")),
                "net_profit": _f(r.get("PARENT_NETPROFIT")),
                "roe": _f(r.get("WEIGHTAVG_ROE")),
                "gross_margin": _f(r.get("XSMLL")),
                "bps": _f(r.get("BPS")),
                "operate_cashflow_ps": _f(r.get("MGJYXJJE")),
                "yoy_revenue": _f(r.get("SJLTZ")),
                "yoy_net_profit": _f(r.get("YSHZ")),
                "industry": _s(r.get("PUBLISHNAME")),
            }
            for r in rows
        ]


class EastmoneyForecastSource(EastmoneyDataCenterSource):
    """业绩预告（东财 datacenter-web ``RPT_PUBLIC_OP_NEWPREDICT``）。

    覆盖「业绩预告 / 快报」日历：预告类型（预增 / 预减 / 扭亏 / 首亏 …）、
    预告净利润区间、变动幅度区间、预告内容文本与去年同期基数。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("forecast", **kwargs)

    def fetch_forecast(
        self,
        *,
        symbol: str = "",
        report_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """业绩预告列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        symbol:
            6 位代码（``600519``）或带前缀（``sh600519``）；空串表示全市场。
        report_date:
            报告期（``2026-06-30``）；空串表示取最新一期。

        Returns
        -------
        ``[{"code","name","notice_date","report_date","predict_type",
        "predict_finance","profit_lower","profit_upper","amp_lower",
        "amp_upper","content","preyear_same_period","forecast_state",
        "market"}, ...]``

        金额单位为元、变动幅度为百分数（``50.0`` 即 +50.00%）。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if report_date:
            # 报表侧报告期字段为完整字面值，含 00:00:00
            rd = report_date if "00:00:00" in report_date else f"{report_date} 00:00:00"
            filters.append(f'REPORT_DATE="{rd}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "code": _s(r.get("SECURITY_CODE")),
                    "name": _s(r.get("SECURITY_NAME_ABBR")),
                    "notice_date": _s(r.get("NOTICE_DATE")),
                    "report_date": _s(r.get("REPORT_DATE")),
                    "predict_type": _s(r.get("PREDICT_TYPE")),
                    "predict_finance": _s(r.get("PREDICT_FINANCE")),
                    "profit_lower": _f(r.get("PREDICT_AMT_LOWER")),
                    "profit_upper": _f(r.get("PREDICT_AMT_UPPER")),
                    "amp_lower": _f(r.get("ADD_AMP_LOWER")),
                    "amp_upper": _f(r.get("ADD_AMP_UPPER")),
                    "content": _s(r.get("PREDICT_CONTENT")),
                    "preyear_same_period": _f(r.get("PREYEAR_SAME_PERIOD")),
                    "forecast_state": _s(r.get("FORECAST_STATE")),
                    "market": _s(r.get("TRADE_MARKET")),
                }
            )
        return out


class EastmoneyIpoSource(EastmoneyDataCenterSource):
    """IPO 申购日历（东财 datacenter-web ``RPTA_APP_IPOAPPLY``）。

    全市场新股/新债申购列表：申购日期与代码、发行价（未定价时给出
    预测价）、发行量、网上申购上限与顶格市值、中签号/缴款/上市日期、
    行业 PE 与发行后 PE。覆盖「今日申购 → 待上市 → 已上市」全生命周期
    字段，可按申购日过滤（「今日申购」视图）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("ipo", **kwargs)

    def fetch_ipo(
        self,
        *,
        apply_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """IPO 申购日历（按申购日期降序；v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        apply_date:
            申购日 ``YYYY-MM-DD``；非空时只取该日（「今日申购」视图），
            空串表示全量倒序分页。
        page, size:
            分页参数（datacenter 报表分页）。

        Returns
        -------
        ``[{"code","name","secucode","apply_code","apply_date",
        "listing_date","ballot_num_date","ballot_pay_date",
        "online_issue_date","trade_market","market_type","issue_price",
        "predict_issue_price","issue_num","online_issue_num",
        "online_apply_upper","top_apply_marketcap","industry_pe",
        "after_issue_pe","bvps","issue_way"}, ...]``

        单位：``issue_num`` 万股；``online_issue_num`` / ``online_apply_upper``
        股；``top_apply_marketcap`` 万元；价格为元。未定价/未上市字段为
        ``None``（与报表侧 ``null`` 一致，不填充为 0）。
        """
        filters: list[str] = []
        if apply_date:
            rd = apply_date if "00:00:00" in apply_date else f"{apply_date} 00:00:00"
            filters.append(f'APPLY_DATE="{rd}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="APPLY_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [self._normalize(r) for r in rows]

    @staticmethod
    def _normalize(r: Mapping[str, Any]) -> dict[str, Any]:
        def _opt(value: Any) -> float | None:
            # null 语义保留：0 与「未定」不同（预测价 0 = 未定价）
            return _f(value) if value is not None else None

        return {
            "code": _s(r.get("SECURITY_CODE")),
            "name": _s(r.get("SECURITY_NAME")),
            "secucode": _s(r.get("SECUCODE")),
            "apply_code": _s(r.get("APPLY_CODE")),
            "apply_date": _s(r.get("APPLY_DATE"))[:10],
            "listing_date": _s(r.get("LISTING_DATE"))[:10],
            "ballot_num_date": _s(r.get("BALLOT_NUM_DATE"))[:10],
            "ballot_pay_date": _s(r.get("BALLOT_PAY_DATE"))[:10],
            "online_issue_date": _s(r.get("ONLINE_ISSUE_DATE"))[:10],
            "trade_market": _s(r.get("TRADE_MARKET")),
            "market_type": _s(r.get("MARKET_TYPE")),
            "issue_price": _opt(r.get("ISSUE_PRICE")),
            "predict_issue_price": _opt(r.get("PREDICT_ISSUE_PRICE")),
            "issue_num": _opt(r.get("ISSUE_NUM")),
            "online_issue_num": _opt(r.get("ONLINE_ISSUE_NUM")),
            "online_apply_upper": _opt(r.get("ONLINE_APPLY_UPPER")),
            "top_apply_marketcap": _opt(r.get("TOP_APPLY_MARKETCAP")),
            "industry_pe": _opt(r.get("INDUSTRY_PE")),
            "after_issue_pe": _opt(r.get("AFTER_ISSUE_PE")),
            "bvps": _opt(r.get("BVPS")),
            "issue_way": _s(r.get("ISSUE_WAY")),
        }
