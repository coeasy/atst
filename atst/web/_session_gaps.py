# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""缺口补全 Mixin（对标 5 个行情项目的真实差距，见
``docs/archive/plans/atst_覆盖与补全方案_对比5项目.md`` 第 3 节 G-01~G-13）。

设计纪律（与 :mod:`atst.web._session_p2` 完全一致）：

* 每个方法都是 ``@staticmethod``，经 :func:`atst.catalog.capability._discover_web_bindings`
  自动登记为能力，天然在 CLI / HTTP / WS / MCP 四面通用分发（不存在漏暴露断链）。
* 东财 datacenter-web 类统一走 :class:`atst.web.corporate.EastmoneyDataCenterSource`；
  巨潮 / 同花顺类走各自已验证基座。
* **诚实性**：标 ``needs_verify`` 的能力其在线端点需真机抓包校准；端点若失效，
  基座会抛 :class:`~atst.errors.SourceDeprecated` / :class:`~atst.errors.WebSourceError`
  （干净失败，绝不崩溃或静默返回空），与其余能力同源降级语义。

所有方法返回 ``list[dict[str, Any]]``，与现有 Mixin 输出契约一致。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._session_market import _shared_http

__all__ = ["GapsSessionMixin"]


def _eastmoney_rows(
    report: str,
    *,
    filters: Sequence[str] = (),
    sort_columns: str = "",
    sort_types: str = "-1",
    page: int = 1,
    size: int = 20,
) -> list[dict[str, Any]]:
    """复用已验证的东财 datacenter-web 报表基类取数。

    报表名若未写入 :data:`atst.web.corporate.VALID_REPORTS`，则原样透传
    （``VALID_REPORTS.get(report, report)``），便于此处 best-effort 引用尚未
    抓包校准的报表名；抓取失败由基类统一抛 ``SourceDeprecated`` / ``WebSourceError``。
    """

    from .corporate import EastmoneyDataCenterSource

    src = EastmoneyDataCenterSource(report=report, client=_shared_http())
    try:
        return src.fetch_rows(
            filters=list(filters),
            sort_columns=sort_columns,
            sort_types=sort_types,
            page=page,
            size=size,
        )
    finally:
        src.close()


class GapsSessionMixin:
    """对标缺口补全（交易日历 / 宏观补全 / ST / 质押 / 互动易 / 新闻联播 /
    估值历史 / 申万行业 / ETF 份额 / 扫雷 / 指数估值 / 期货期权持仓排名）。
    """

    # -- G-01 交易日历（内置，离线可用） ---------------------------------- #
    @staticmethod
    def trade_calendar(
        *,
        start: str = "",
        end: str = "",
        market: str = "cn",
    ) -> list[dict[str, Any]]:
        """A 股交易日历（内置表 2024–2026，2026 为估算值）。

        Parameters
        ----------
        start, end:
            起止日期（``YYYY-MM-DD``）；同时给出时返回区间内的交易日列表，
            否则返回各年份的休市日摘要。
        market:
            预留市场参数（当前仅 ``"cn"`` A 股）。

        Returns
        -------
        区间模式：``[{"date","trading":True}, ...]``；
        摘要模式：``[{"year","estimated","holidays":[...],"trading_day_count"}, ...]``

        .. note::
            2026 年为估算值，不得作为交易决策唯一依据（与 ``domain/calendar`` 口径一致）。
        """
        from datetime import date

        from ..domain.calendar import (
            BUILTIN_CALENDARS,
            ESTIMATED_YEARS,
            trading_days_between,
        )

        market_norm = (market or "cn").strip().lower()
        if market_norm != "cn":
            raise ValueError(
                f"trade_calendar 目前只支持 A 股 market='cn'，收到 {market!r}；"
                "其它市场需先登记对应的内置日历表"
            )

        if start and end:
            days = trading_days_between(start, end)
            return [{"date": d.isoformat(), "trading": True} for d in days]
        out: list[dict[str, Any]] = []
        for year in sorted(BUILTIN_CALENDARS):
            holidays = sorted(BUILTIN_CALENDARS[year])
            days = trading_days_between(date(year, 1, 1), date(year, 12, 31))
            count = len(days)
            out.append(
                {
                    "year": year,
                    "estimated": year in ESTIMATED_YEARS,
                    "holidays": holidays,
                    "trading_day_count": count,
                }
            )
        return out

    # -- G-03 宏观补全（社融 / PMI / LPR / 中债曲线 / 回购定盘） ---------- #
    # 以下报表名 best-effort，需真机抓包校准（needs_verify）。
    @staticmethod
    def macro_social_financing(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """社融存量 / 增量（人民银行口径，best-effort 端点）。"""
        return _eastmoney_rows(
            "RPT_ECONOMY_SFS",
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def macro_pmi(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """制造业 / 非制造业 PMI（国家统计局口径，best-effort 端点）。"""
        return _eastmoney_rows(
            "RPT_ECONOMY_PMI",
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def macro_lpr(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """LPR 贷款市场报价利率（1 年 / 5 年，best-effort 端点）。"""
        return _eastmoney_rows(
            "RPT_ECONOMY_LPR",
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def macro_bond_yield(*, term: str = "", size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """中债国债收益率曲线（多期限，best-effort 端点）。

        term: 期限过滤（如 ``"10Y"``）；空串=全期限。
        """
        filters = [f'TERM="{term}"'] if term else ()
        return _eastmoney_rows(
            "RPT_BOND_YIELD_CURVE",
            filters=filters,
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def macro_repo_rate(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """回购定盘利率（DR007 / FR007 等，best-effort 端点）。"""
        return _eastmoney_rows(
            "RPT_REPO_FIXING_RATE",
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    # -- G-05 ST / *ST 名单 ------------------------------------------------ #
    @staticmethod
    def st_list(*, date: str = "", size: int = 200, page: int = 1) -> list[dict[str, Any]]:
        """ST / *ST 风险警示股名单（含现价，best-effort 端点）。

        date: 基准日（``YYYY-MM-DD``）；空串=最新。
        """
        filters = [f'TRADE_DATE="{date}"'] if date else ()
        return _eastmoney_rows(
            "RPT_STOCK_ST_LIST",
            filters=filters,
            sort_columns="SECURITY_CODE",
            sort_types="1",
            page=page,
            size=size,
        )

    # -- G-06 股权质押 ------------------------------------------------------ #
    @staticmethod
    def equity_pledge(
        *, symbol: str = "", date: str = "", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """股权质押明细（质押股数 / 比例 / 到期日，best-effort 端点）。

        symbol: 6 位代码或带前缀；空串=全市场。date: 基准日过滤。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if date:
            filters.append(f'PLEDGE_DATE="{date}"')
        return _eastmoney_rows(
            "RPT_EQUITY_PLEDGE",
            filters=filters,
            sort_columns="PLEDGE_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    # -- G-07 互动易 / 上证 e 互动问答 ------------------------------------ #
    @staticmethod
    def interactive_qa(
        *, symbol: str = "", keyword: str = "", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """互动易 / e 互动问答（上市公司投资者关系，best-effort 端点）。

        symbol: 6 位代码；keyword: 问题关键词；二者可任选其一或同时给。
        """
        from .cninfo.adapters import CninfoSource

        src = CninfoSource(client=_shared_http())
        try:
            return src.fetch_interactive_qa(symbol=symbol, keyword=keyword, page=page, size=size)
        finally:
            src.close()

    # -- G-04 新闻联播文字稿 ---------------------------------------------- #
    @staticmethod
    def news_broadcast(*, date: str = "", size: int = 20, page: int = 1) -> list[dict[str, Any]]:
        """央视《新闻联播》文字稿（best-effort 端点）。

        date: 日期（``YYYY-MM-DD``）；空串=最新一期。
        """
        from .news import fetch_cctv_news_broadcast

        return fetch_cctv_news_broadcast(date=date, page=page, size=size)

    # -- G-08 估值历史日频序列 -------------------------------------------- #
    @staticmethod
    def valuation_history(symbol: str, *, count: int = 120) -> list[dict[str, Any]]:
        """估值历史序列（PE-TTM / PB / PS-TTM 日频，回溯多年，best-effort 端点）。

        区别于快照式 ``stock_valuation``，本方法返回按交易日降序的多期序列。
        """
        return _eastmoney_rows(
            "RPT_VALUEASSESS_HIS",
            filters=[f'SECURITY_CODE="{symbol}"'],
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=1,
            size=count,
        )

    # -- G-09 申万行业 + 行业变迁史 --------------------------------------- #
    @staticmethod
    def sw_industry(*, symbol: str = "", size: int = 200, page: int = 1) -> list[dict[str, Any]]:
        """申万一级/二级行业分类（best-effort 端点）。

        symbol: 6 位代码；空串=全市场行业映射。
        """
        from ..domain.symbol import split_symbol

        filters = []
        if symbol:
            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        return _eastmoney_rows(
            "RPT_SW_INDUSTRY",
            filters=filters,
            sort_columns="SECURITY_CODE",
            sort_types="1",
            page=page,
            size=size,
        )

    @staticmethod
    def sw_industry_history(symbol: str, *, size: int = 100, page: int = 1) -> list[dict[str, Any]]:
        """个股申万行业变迁史（避免前视偏差，best-effort 端点）。"""
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        return _eastmoney_rows(
            "RPT_SW_INDUSTRY_HIS",
            filters=[f'SECURITY_CODE="{code}"'],
            sort_columns="EFFECT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    # -- G-10 ETF 份额 ----------------------------------------------------- #
    @staticmethod
    def etf_shares(
        *, symbol: str = "", date: str = "", size: int = 200, page: int = 1
    ) -> list[dict[str, Any]]:
        """ETF 场内份额（万份，日频，best-effort 端点）。

        symbol: 6 位代码；date: 基准日；空串=最新。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if date:
            filters.append(f'END_DATE="{date}"')
        return _eastmoney_rows(
            "RPT_FUND_ETF_SHARES",
            filters=filters,
            sort_columns="END_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    # -- G-11 扫雷 / 风险扫描 --------------------------------------------- #
    @staticmethod
    def risk_scan(symbol: str, *, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """个股风险扫描（诉讼 / 违规 / 质押 / 商誉等雷点，best-effort 端点）。"""
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        return _eastmoney_rows(
            "RPT_STOCK_RISK_SCAN",
            filters=[f'SECURITY_CODE="{code}"'],
            sort_columns="NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    # -- G-13 指数估值 PE / 股息率 ---------------------------------------- #
    @staticmethod
    def index_valuation(
        *, index_code: str = "", size: int = 200, page: int = 1
    ) -> list[dict[str, Any]]:
        """指数估值（PE / PB / 股息率，中证两种股本口径，best-effort 端点）。

        index_code: 指数代码（如 ``000300``）；空串=全指数估值快照。
        """
        filters = [f'INDEX_CODE="{index_code}"'] if index_code else ()
        return _eastmoney_rows(
            "RPT_INDEX_VALUATION",
            filters=filters,
            sort_columns="INDEX_CODE",
            sort_types="1",
            page=page,
            size=size,
        )

    # -- G-02 期货 / 期权会员持仓排名 ------------------------------------- #
    @staticmethod
    def futures_position_rank(
        *, symbol: str = "", date: str = "", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """期货会员持仓排名（交易所官方，best-effort 端点）。

        symbol: 合约代码；date: 交易日期；空串=最新排名。
        """
        filters: list[str] = []
        if symbol:
            filters.append(f'INSTRUMENT_ID="{symbol}"')
        if date:
            filters.append(f'TRADE_DATE="{date}"')
        return _eastmoney_rows(
            "RPT_FUTURES_POSITION_RANK",
            filters=filters,
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def options_position_rank(
        *, symbol: str = "", date: str = "", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """期权会员持仓排名（交易所官方，best-effort 端点）。

        symbol: 期权合约代码；date: 交易日期；空串=最新排名。
        """
        filters: list[str] = []
        if symbol:
            filters.append(f'OPTION_ID="{symbol}"')
        if date:
            filters.append(f'TRADE_DATE="{date}"')
        return _eastmoney_rows(
            "RPT_OPTIONS_POSITION_RANK",
            filters=filters,
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )
