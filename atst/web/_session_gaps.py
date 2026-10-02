# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""缺口补全 Mixin（对标 5 个行情项目的真实差距，见
``docs/archive/plans/atst_覆盖与补全方案_对比5项目.md`` 第 3 节 G-01~G-13）。

设计纪律（与 :mod:`atst.web._session_p2` 完全一致）：

* 每个方法都是 ``@staticmethod``，经 :func:`atst.catalog.capability._discover_web_bindings`
  自动登记为能力，天然在 CLI / HTTP / WS / MCP 四面通用分发（不存在漏暴露断链）。
* 东财 datacenter-web 类统一走 :class:`atst.web.corporate.EastmoneyDataCenterSource`；
  push2 板块/ETF 行情走 :class:`atst.web.boards.EastmoneyBoardSource` 与共享 HTTP 池；
  巨潮 / 央视 / 外汇交易中心 / 商务部走各自已验证基座。
* **诚实性**：仍标 ``needs_verify`` 的能力（互动易 / 新闻联播 / 申万分类 /
  指数估值 / 期货期权持仓排名）其在线端点尚未真机抓包校准；端点若失效，
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


def _em_clist_rows(
    fs: str,
    fields: str,
    *,
    page: int = 1,
    size: int = 200,
    fid: str = "f12",
    host: str = "https://push2.eastmoney.com",
) -> list[dict[str, str | float]]:
    """东财 push2 ``clist`` 通用行查询（板块成分 / ETF 现货等）。

    失败统一抛 :class:`~atst.errors.WebSourceError`（干净失败语义与其余
    web 源一致），绝不静默返回空。
    """
    from urllib.parse import urlencode

    from .boards import EastmoneyBoardSource

    src = EastmoneyBoardSource(client=_shared_http())
    try:
        src.rate_limiter.acquire(src.source_name)
        payload = src._get_json(  # noqa: SLF001 —— 同包内复用已验证的取数/报错链
            f"/api/qt/clist/get?{urlencode({'pn': max(1, page), 'pz': max(1, min(size, 500)), 'po': 1, 'np': 1, 'fltt': 2, 'invt': 2, 'fid': fid, 'fs': fs, 'fields': fields})}"
        )
    finally:
        src.close()
    rows = EastmoneyBoardSource._diff(payload)  # noqa: SLF001
    return [dict(r) for r in rows]


def _pager(rows: list[dict[str, Any]], *, size: int, page: int) -> list[dict[str, Any]]:
    """对上游不分页的全量行做客户端分页（最新在前）。"""
    start = (max(1, page) - 1) * max(1, size)
    return rows[start : start + max(1, size)]


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

    # -- G-03 宏观补全（社融 / PMI / LPR / 国债收益率 / 回购定盘） -------- #
    # LPR / 国债收益率为东财 datacenter 已验证报表；社融走商务部数据中心、
    # 回购定盘走外汇交易中心 CSV，均已真机验证（alive）。
    @staticmethod
    def macro_social_financing(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """社会融资规模增量（人民银行口径，商务部数据中心月度）。

        Returns
        -------
        ``[{"month","sfs_increment","rmb_loans","forex_loans","entrust_loans",
        "trust_loans","undiscounted_bankers","corporate_bonds","equity_financing"}, ...]``
        （单位：亿元；上游按月全量返回，这里客户端分页、最新在前。）
        """
        from ..errors import WebSourceError

        url = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
        resp = _shared_http().post(url, body=b"", content_type="application/x-www-form-urlencoded")
        if not resp.ok:
            raise WebSourceError(f"商务部社融数据请求失败 HTTP {resp.status} (source='mofcom')")
        try:
            raw = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WebSourceError("商务部社融数据解析失败 (source='mofcom')") from exc
        if not isinstance(raw, list):
            raise WebSourceError("商务部社融数据返回结构异常 (source='mofcom')")
        rows = [
            {
                "month": str(r.get("date", "")),
                "sfs_increment": r.get("tiosfs"),
                "rmb_loans": r.get("rmblaon"),
                "forex_loans": r.get("forcloan"),
                "entrust_loans": r.get("entrustloan"),
                "trust_loans": r.get("trustloan"),
                "undiscounted_bankers": r.get("ndbab"),
                "corporate_bonds": r.get("bibae"),
                "equity_financing": r.get("sfinfe"),
            }
            for r in raw
            if isinstance(r, dict)
        ]
        # mofcom 上游即最新在前（实测 202604 行首），直接分页
        return _pager(rows, size=size, page=page)

    @staticmethod
    def macro_pmi(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """制造业 / 非制造业 PMI（国家统计局口径，东财 ``RPT_ECONOMY_PMI``）。"""
        return _eastmoney_rows(
            "RPT_ECONOMY_PMI",
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    @staticmethod
    def macro_lpr(*, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """LPR 贷款市场报价利率（1 年 / 5 年，东财 ``RPTA_WEB_RATE``，已验证）。"""
        return _eastmoney_rows(
            "RPTA_WEB_RATE",
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )

    #: 国债收益率报表字段 → 期限（akshare ``bond_zh_us_rate`` 同源映射）。
    _TREASURY_FIELDS: dict[str, str] = {
        "EMM00588704": "cn_2y",
        "EMM00166462": "cn_5y",
        "EMM00166466": "cn_10y",
        "EMM00166469": "cn_30y",
        "EMM01276014": "cn_10y_2y",
        "EMG00001306": "us_2y",
        "EMG00001308": "us_5y",
        "EMG00001310": "us_10y",
        "EMG00001312": "us_30y",
        "EMG01339436": "us_10y_2y",
        "EMM00000024": "cn_gdp_yoy",
        "EMG00159635": "us_gdp_yoy",
    }

    @staticmethod
    def macro_bond_yield(*, term: str = "", size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """中/美国债收益率日频序列（东财 ``RPTA_WEB_TREASURYYIELD``，已验证）。

        term:
            期限选择：``""``（全字段）/ ``cn_2y`` / ``cn_5y`` / ``cn_10y`` /
            ``cn_30y`` / ``us_2y`` / ``us_5y`` / ``us_10y`` / ``us_30y`` 等
            （取值即返回行的键名）。给定单个期限时只返回
            ``[{"date","term","yield"}, ...]``。
        """
        rows = _eastmoney_rows(
            "RPTA_WEB_TREASURYYIELD",
            sort_columns="SOLAR_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )
        term_norm = (term or "").strip().lower()
        out: list[dict[str, Any]] = []
        for r in rows:
            date = str(r.get("SOLAR_DATE", ""))[:10]
            renamed = {
                "date": date,
                **{
                    GapsSessionMixin._TREASURY_FIELDS[k]: v
                    for k, v in r.items()
                    if k in GapsSessionMixin._TREASURY_FIELDS
                },
            }
            if term_norm:
                if term_norm not in renamed:
                    raise ValueError(
                        f"macro_bond_yield term 只接受 {sorted(GapsSessionMixin._TREASURY_FIELDS.values())}，"
                        f"收到 {term!r}"
                    )
                out.append({"date": date, "term": term_norm, "yield": renamed[term_norm]})
            else:
                out.append(renamed)
        return out

    @staticmethod
    def macro_repo_rate(
        *, kind: str = "frr", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """回购定盘利率（外汇交易中心 chinamoney CSV，已验证）。

        kind:
            ``"frr"``（回购定盘利率 FR001/FR007/FR014）或 ``"fdr"``
            （银银间回购定盘利率 FDR001/FDR007/FDR014）。
        """
        from urllib.parse import quote

        from ..errors import WebSourceError

        kind_norm = (kind or "frr").strip().lower()
        if kind_norm not in ("frr", "fdr"):
            raise ValueError(f"macro_repo_rate kind 只接受 'frr'/'fdr'，收到 {kind!r}")
        url = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/" + (
            "frr-chrt.csv" if kind_norm == "frr" else "fdr-chrt.csv"
        )
        cols = ("FR001", "FR007", "FR014") if kind_norm == "frr" else ("FDR001", "FDR007", "FDR014")
        resp = _shared_http().get(quote(url, safe=":/?=&"))
        if not resp.ok:
            raise WebSourceError(
                f"chinamoney 回购定盘利率请求失败 HTTP {resp.status} (source='chinamoney')"
            )
        rows: list[dict[str, Any]] = []
        for line in resp.text().splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            # CSV 混有空列（akshare 同款 dropna(axis=1) 语义）：先剔除空段再映射
            if len(parts) < 4 or not parts[0][:4].isdigit():
                continue  # 表头/空行
            row: dict[str, Any] = {"date": parts[0]}
            row.update({cols[i]: parts[i + 1] or None for i in range(len(cols))})
            rows.append(row)
        if not rows:
            raise WebSourceError("chinamoney 回购定盘利率返回为空 (source='chinamoney')")
        # chinamoney CSV 上游即最新在前（实测首行 2026-09-30），直接分页
        return _pager(rows, size=size, page=page)

    # -- G-05 ST / *ST 名单 ------------------------------------------------ #
    @staticmethod
    def st_list(*, limit: int = 200, page: int = 1) -> list[dict[str, Any]]:
        """ST / *ST 风险警示股名单（东财风险警示板 BK0511 实时成分，已验证）。

        Returns
        -------
        ``[{"code","name","price","pct_change"}, ...]``（最新快照，无历史基准日）。
        """
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_members("BK0511", limit=limit, page=page)
        finally:
            src.close()

    # -- G-06 股权质押 ------------------------------------------------------ #
    @staticmethod
    def equity_pledge(
        *, symbol: str = "", date: str = "", size: int = 50, page: int = 1
    ) -> list[dict[str, Any]]:
        """股权质押比例明细（中证登口径，东财 ``RPT_CSDC_LIST``，已验证）。

        symbol: 6 位代码或带前缀；空串=全市场。date: 基准日（``YYYY-MM-DD``）过滤。

        Returns
        -------
        ``[{"security_code","security_name_abbr","pledge_ratio","pledge_market_cap",
        "trade_date", ...}, ...]``（字段名为报表原始键）。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if date:
            filters.append(f'TRADE_DATE="{date}"')
        return _eastmoney_rows(
            "RPT_CSDC_LIST",
            filters=filters,
            sort_columns="TRADE_DATE",
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
        """估值历史序列（PE-TTM / PB / PS-TTM 日频，东财 ``RPT_VALUEANALYSIS_DET``，已验证）。

        区别于快照式 ``stock_valuation``，本方法返回按交易日降序的多期序列
        （PE_TTM / PB_MRQ / PS_TTM / PCF_OCF_TTM / 股息率等字段随报表给出）。
        """
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        return _eastmoney_rows(
            "RPT_VALUEANALYSIS_DET",
            filters=[f'SECURITY_CODE="{code}"'],
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=1,
            size=count,
        )

    # -- G-09 申万行业 + 行业变迁史 --------------------------------------- #
    @staticmethod
    def sw_industry(*, symbol: str = "", size: int = 200, page: int = 1) -> list[dict[str, Any]]:
        """申万一级/二级行业分类（best-effort 端点，东财无公开申万分类报表，待校准）。

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
    def etf_shares(*, symbol: str = "", size: int = 200, page: int = 1) -> list[dict[str, Any]]:
        """ETF 场内最新份额（东财 push2 ETF 现货列表 ``f38``，已验证）。

        symbol: 6 位代码（客户端过滤）；空串=全市场 ETF。

        Returns
        -------
        ``[{"code","market","name","price","pct_change","shares"}, ...]``
        （``shares`` 单位：万份，与东财行情页「最新份额」一致。）
        """
        rows: list[dict[str, str | float]] = []
        # 全市场 ETF ~1400 只 > 单页 500：逐页拉全（fid=f12 升序保证稳定覆盖）
        for page_no in (1, 2, 3, 4):
            page_rows = _em_clist_rows(
                "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
                "f12,f13,f14,f2,f3,f38",
                page=page_no,
                size=500,
            )
            if not page_rows:
                break
            rows.extend(page_rows)
        out: list[dict[str, Any]] = [
            {
                "code": r.get("f12", ""),
                "market": r.get("f13", ""),
                "name": r.get("f14", ""),
                "price": r.get("f2"),
                "pct_change": r.get("f3"),
                "shares": r.get("f38"),
            }
            for r in rows
            if isinstance(r, dict)
        ]
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            out = [r for r in out if str(r.get("code", "")) == code]
            if not out:
                raise ValueError(f"未找到 ETF {code!r}（etf_shares 仅覆盖场内基金）")
        return _pager(out, size=size, page=page)

    # -- G-11 扫雷 / 风险扫描 --------------------------------------------- #
    @staticmethod
    def risk_scan(symbol: str, *, size: int = 50, page: int = 1) -> list[dict[str, Any]]:
        """个股风险扫描（已验证报表复合：商誉明细 + 股权质押比例）。

        聚合东财 ``RPT_GOODWILL_STOCKDETAILS``（商誉）与 ``RPT_CSDC_LIST``
        （质押比例）两类已验证报表，按 ``kind`` 区分风险条目；后续雷点
        （诉讼 / 违规处罚）待对应端点校准后追加。
        """
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        items: list[dict[str, Any]] = []
        for kind, report, sort_col in (
            ("goodwill", "RPT_GOODWILL_STOCKDETAILS", "NOTICE_DATE"),
            ("pledge", "RPT_CSDC_LIST", "TRADE_DATE"),
        ):
            try:
                rows = _eastmoney_rows(
                    report,
                    filters=[f'SECURITY_CODE="{code}"'],
                    sort_columns=sort_col,
                    sort_types="-1",
                    page=page,
                    size=size,
                )
            except Exception:  # noqa: BLE001 —— 单类雷点失败不拖垮整体扫描
                rows = []
            items.extend({"kind": kind, **r} for r in rows)
        return items

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
