# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""三大财务报表（资产负债表 / 利润表 / 现金流量表）。

补齐 tstdx 此前缺失的**报表级**财务明细——:meth:`UnifiedQuoteAPI.financial_abstract`
只给「财务主要指标摘要」的汇总口径（EPS / ROE / 营收同比 / 毛利率 / 资产负债率等），
无法做应收账款与长期应收款坏账风险、经营现金流质量、有息负债结构、研发费用
占收入比等深度分析。本模块直连东财 datacenter-web 报表族，返回**全字段明细**。

接口事实（2026-09 抓包验证，tstdx 自有实现）::

    https://datacenter-web.eastmoney.com/api/data/v1/get
      ?reportName=RPT_F10_FINANCE_GBALANCE&columns=ALL
      &pageSize=10&pageNumber=1&sortColumns=REPORT_DATE&sortTypes=-1
      &source=WEB&client=WEB
      &filter=(SECUCODE%3D%22600519.SH%22)

    // 成功:
    {"result":{"pages":3,"data":[{"SECUCODE":"600519.SH",
        "SECURITY_CODE":"600519","SECURITY_NAME_ABBR":"贵州茅台",
        "REPORT_DATE":"2026-06-30 00:00:00","REPORT_TYPE":"中报",
        "NOTICE_DATE":"2026-08-15 00:00:00",
        "TOTAL_ASSETS":309050784569.31, ...}],"count":29},
     "success":true,"message":"ok","code":0}

    // 失败（报表名拼错 / 已下线）:
    {"result":null,"success":false,
     "message":"报表配置不存在,RPT_XXX","code":9501}

与 :mod:`tstdx.web.corporate` 的 datacenter-web 报表族**同源**——同主机池
failover + 黑名单 + 失败计数 + 分页逻辑，唯一差异是个股过滤键为 ``SECUCODE``
（大写市场后缀 ``600519.SH``），非 ``SECURITY_CODE``（6 位纯代码）——故不可
复用 :meth:`EastmoneyDataCenterSource.fetch_rows_by_code`，需手动构造 filter。

.. note::
   报表名为 best-effort 映射（与 :mod:`tstdx.web.astock_toolkit`
   的 ``RPT_VALUEASSESS_DET`` 同策略）——东财报表名偶发变动，若服务端返回
   「报表配置不存在 (code=9501)」需重新抓包校准。字段名采用 2026-09 真实
   抓包列名 + 别名容错（见 :data:`FIELD_MAPS`），未命中列由
   :meth:`fetch_report` 原样透传，不丢数据。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .corporate import EastmoneyDataCenterSource
from .sources import CORPORATE

__all__ = [
    "EastmoneyF10ReportSource",
    "F10_REPORTS",
    "FIELD_MAPS",
    "to_eastmoney_secucode",
]


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _num(value: Any) -> float | None:
    """数值容错：None / 空 / 非数 → None（保留 null 语义，不填 0）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        s = str(value).strip().replace(",", "").replace("%", "")
        if not s or s in {"-", "--", "None", "nan"}:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    return None if v != v else v  # NaN → None


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    """从行中取首个存在的列名对应值（别名容错）。"""
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def to_eastmoney_secucode(symbol: str) -> str:
    """东财 ``SECUCODE``：``600519.SH`` / ``000001.SZ`` / ``830799.BJ``。

    F10 报表族用大写市场后缀过滤（与 :func:`tstdx.web.base.to_eastmoney_secid`
    的 ``1.600519`` 点分式不同），故单独实现。
    """
    from ..domain.symbol import split_symbol

    market, code = split_symbol(symbol)
    suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market, "SZ")
    return f"{code}.{suffix}"


# --------------------------------------------------------------------------- #
# 报表定义与字段映射
# --------------------------------------------------------------------------- #
#: 三大报表别名 → datacenter-web 报表名（2026-09 抓包验证可用）
F10_REPORTS: dict[str, str] = {
    "balance_sheet": "RPT_F10_FINANCE_GBALANCE",
    "income_sheet": "RPT_F10_FINANCE_GINCOME",
    "cash_flow": "RPT_F10_FINANCE_GCASHFLOW",
}


def _resolve_report(report: str) -> str:
    """别名 → 报表名；非别名原样返回。"""
    return F10_REPORTS.get(report, report)


#: 资产负债表字段映射（归一化键 → 真实列名候选；金额单位元）
_BALANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "total_assets": ("TOTAL_ASSETS", "TOTAL_CAPITAL"),
    "total_liab": ("TOTAL_LIABILITIES", "TOTAL_LIAB"),
    "total_equity": ("TOTAL_EQUITY", "TOTAL_PARENT_EQUITY"),
    "monetary_funds": ("MONETARYFUNDS", "CURRENCY_FUND", "MONETARY_FUNDS"),
    "accounts_recv": ("ACCOUNTS_RECE", "ACCOUNTS_RECEIVABLE"),
    "long_recv": ("LONG_EQUITY_RECE", "LONG_ACCOUNTS_RECE", "LONG_TERM_RECEIVABLE"),
    "inventory": ("INVENTORY", "INVENTORIES"),
    "goodwill": ("GOODWILL",),
    "fixed_asset": ("FIXED_ASSET", "FIXED_ASSETS"),
    "long_eq_invest": ("LONG_EQUITY_INVEST", "LONG_EQUITY_INVESTMENT"),
    "short_loan": ("SHORT_LOAN", "SHORT_TERM_LOAN", "SHORT_TERM_BORROWING"),
    "long_loan": ("LONG_LOAN", "LONG_TERM_LOAN", "LONG_TERM_BORROWING"),
    "minority_equity": ("MINORITY_EQUITY", "MINORITY_INTEREST_EQUITY"),
}

#: 利润表字段映射（金额单位元、EPS 单位元/股）
_INCOME_FIELDS: dict[str, tuple[str, ...]] = {
    "operate_income": ("TOTAL_OPERATE_INCOME", "TOTAL_OPER_REV", "OPERATE_INCOME"),
    "operate_cost": ("TOTAL_OPERATE_COST", "TOTAL_OPER_COST", "OPERATE_COST"),
    "sell_expense": ("SALE_EXPENSE", "SELL_EXPENSE"),
    "manage_expense": ("MANAGE_EXPENSE", "MGMT_EXPENSE"),
    "rd_expense": ("RESEARCH_EXPENSE", "RD_EXPENSE", "TECHNOLOGY_EXPENSE"),
    "finance_expense": ("FINANCE_EXPENSE", "FIN_EXPENSE"),
    "profit_total": ("TOTAL_PROFIT", "PROFIT_TOTAL", "OPERATE_PROFIT"),
    "net_profit": ("NETPROFIT", "TOTAL_NETPROFIT", "NET_PROFIT"),
    "parent_net_profit": ("PARENT_NETPROFIT", "NETPROFIT_PARENT", "NET_PROFIT_PARENT"),
    "minority_profit": ("MINORITY_INTEREST", "MINORITY_PROFIT"),
    "basic_eps": ("BASIC_EPS", "EPS_BASIC"),
    "diluted_eps": ("DILUTED_EPS", "EPS_DILUTED"),
}

#: 现金流量表字段映射（金额单位元；2026-09 真实列名优先）
_CASHFLOW_FIELDS: dict[str, tuple[str, ...]] = {
    "sale_cash_in": ("SALES_SERVICES", "SALE_GOODS_SERVICE_CASH", "SALE_RECIEVE_CASH"),
    "cash_recv_operate_in": ("TOTAL_OPERATE_INFLOW", "CASH_REC_OPERATE_NET"),
    "cash_pay_operate_out": ("TOTAL_OPERATE_OUTFLOW", "BUY_SERVICES", "BUY_GOODS_CASH"),
    "cash_pay_operate_net": ("TOTAL_OPERATE_OUTFLOW", "CASH_PAY_OPERATE_NET"),
    "cash_operate_net": ("NETCASH_OPERATE", "TOTAL_OPERATE_CASH", "NET_OPERATE_CASH"),
    "cash_invest_net": ("NETCASH_INVEST", "TOTAL_INVEST_CASH", "NET_INVEST_CASH"),
    "cash_finance_net": ("NETCASH_FINANCE", "TOTAL_FINANCE_CASH", "NET_FINANCE_CASH"),
    "cash_add": ("CCE_ADD", "CASH_ADD", "NET_CASH_ADD"),
    "cash_begin": ("BEGIN_CASH", "BEGIN_CCE", "BEGINNING_CASH"),
    "cash_end": ("END_CASH", "END_CCE", "ENDING_CASH"),
}

#: 报表别名 → 字段映射表
FIELD_MAPS: dict[str, dict[str, tuple[str, ...]]] = {
    "balance_sheet": _BALANCE_FIELDS,
    "income_sheet": _INCOME_FIELDS,
    "cash_flow": _CASHFLOW_FIELDS,
}

#: 报告期 / 公告日字段候选（三表共用）
_DATE_FIELDS: tuple[str, ...] = ("REPORT_DATE", "END_DATE", "DATE")
_ANN_FIELDS: tuple[str, ...] = ("ANN_DATE", "NOTICE_DATE", "UPDATE_DATE")
_CODE_FIELDS: tuple[str, ...] = ("SECURITY_CODE", "SECUCODE")
_NAME_FIELDS: tuple[str, ...] = ("SECURITY_NAME_ABBR", "SECURITY_NAME")


# --------------------------------------------------------------------------- #
# F10 报表源（继承 datacenter-web 基类）
# --------------------------------------------------------------------------- #
class EastmoneyF10ReportSource(EastmoneyDataCenterSource):
    """三大财务报表（东财 datacenter-web F10 报表族）。

    与 :class:`~tstdx.web.corporate.EastmoneyDataCenterSource` 同源——同主机池、
    同错误处理、同分页逻辑。唯一差异：个股过滤键为 ``SECUCODE``（``600519.SH``），
    非 ``SECURITY_CODE``（6 位纯代码），故 ``fetch_report`` 手动构造 filter，
    不调用 ``fetch_rows_by_code``。

    Parameters
    ----------
    report:
        报表别名（:data:`F10_REPORTS` 键）或原始报表名；仅作默认值，
        实际查询通过 :meth:`fetch_report` 的 ``report`` 参数指定。
    **kwargs:
        :class:`~tstdx.web._base_core.BaseWebSource` 参数（``client`` /
        ``timeout`` / ``max_retries`` / ``rate_limit`` / ``cookie``）。
    """

    JSON_LABEL = "F10报表"
    #: 服务端单页上限（保守取值，超出可能返回 400）
    PAGE_CAP = 50

    #: 报表别名 → 字段映射（见 :data:`FIELD_MAPS`）
    FIELD_MAPS = FIELD_MAPS

    def __init__(self, report: str = "balance_sheet", **kwargs: Any) -> None:
        super().__init__(_resolve_report(report), **kwargs)

    @property
    def source_name(self) -> str:
        return CORPORATE

    # -- 通用报表直查（原始透传） -------------------------------------------- #
    def fetch_report(
        self,
        symbol: str,
        report: str = "balance_sheet",
        *,
        report_date: str = "",
        page: int = 1,
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """通用 F10 报表直查（字段**原样透传**，单位以东财报表页为准）。

        参数
        ----
        symbol:
            6 位代码或带前缀（``600519`` / ``sh600519``）；空串表示全市场最新一期。
        report:
            报表别名（:data:`F10_REPORTS` 键）或原始报表名；非别名原样透传，
            服务端会拒绝已下线报表（报「报表配置不存在」）。
        report_date:
            报告期（``2024-12-31``）；空串表示最新一期起分页。
        size:
            单页条数（上限 :attr:`PAGE_CAP`）。
        all_pages:
            ``True`` 时按报告期降序拉全历史。
        max_pages:
            ``all_pages=True`` 时的防御上限。
        """
        filters: list[str] = []
        if symbol:
            filters.append(f'SECUCODE="{to_eastmoney_secucode(symbol)}"')
        if report_date:
            rd = report_date if "00:00:00" in report_date else f"{report_date} 00:00:00"
            filters.append(f'REPORT_DATE="{rd}"')
        return self.fetch_rows(
            filters=filters,
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page=page,
            size=min(size, self.PAGE_CAP),
            report=_resolve_report(report),
            all_pages=all_pages,
            max_pages=max_pages,
        )

    # -- 归一化明细 ---------------------------------------------------------- #
    def _normalize_rows(
        self, rows: Sequence[Mapping[str, Any]], alias: str
    ) -> list[dict[str, Any]]:
        """按 :data:`FIELD_MAPS` 归一化；未命中列不丢，仅不列入结果。"""
        fields = self.FIELD_MAPS.get(alias, {})
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            rec: dict[str, Any] = {
                "code": _s(_pick(r, *_CODE_FIELDS)),
                "name": _s(_pick(r, *_NAME_FIELDS)),
                "report_date": _s(_pick(r, *_DATE_FIELDS)),
                "ann_date": _s(_pick(r, *_ANN_FIELDS)),
                "report_type": _s(r.get("REPORT_TYPE")),
            }
            for key, names in fields.items():
                rec[key] = _num(_pick(r, *names))
            out.append(rec)
        return out

    def _fetch_alias(
        self,
        symbol: str,
        alias: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self.fetch_report(
            symbol,
            alias,
            report_date=report_date,
            page=1,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        out = self._normalize_rows(rows, alias)
        if not raw:
            return out
        # raw=True：归一化键 + 原始行透传（键 ``raw``）
        for i, rec in enumerate(out):
            if i < len(rows):
                rec["raw"] = dict(rows[i])
        return out

    def fetch_balance_sheet(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """资产负债表明细（按报告期降序）。

        应收账款 / 长期应收款坏账风险、有息负债结构、商誉占净资产比等
        排雷分析的结构化入口。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "total_assets","total_liab","total_equity","monetary_funds",
        "accounts_recv","long_recv","inventory","goodwill","fixed_asset",
        "long_eq_invest","short_loan","long_loan","minority_equity"}, ...]``
        （金额单位元、``float`` 或 ``None``）
        """
        return self._fetch_alias(
            symbol, "balance_sheet",
            report_date=report_date, size=size,
            all_pages=all_pages, max_pages=max_pages, raw=raw,
        )

    def fetch_income_sheet(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """利润表明细（按报告期降序）。

        研发费用占收入比、三费结构、净利率与少数股东损益占比等
        盈利质量分析入口。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "operate_income","operate_cost","sell_expense","manage_expense",
        "rd_expense","finance_expense","profit_total","net_profit",
        "parent_net_profit","minority_profit","basic_eps","diluted_eps"}, ...]``
        （金额单位元、EPS 单位元/股、``float`` 或 ``None``）
        """
        return self._fetch_alias(
            symbol, "income_sheet",
            report_date=report_date, size=size,
            all_pages=all_pages, max_pages=max_pages, raw=raw,
        )

    def fetch_cash_flow(
        self,
        symbol: str,
        *,
        report_date: str = "",
        size: int = 10,
        all_pages: bool = False,
        max_pages: int = 100,
        raw: bool = False,
    ) -> list[dict[str, Any]]:
        """现金流量表明细（按报告期降序）。

        经营现金流与净利润的匹配度（现金流质量）、投资与筹资活动净额、
        自由现金流等分析入口。

        Returns
        -------
        ``[{"code","name","report_date","ann_date","report_type",
        "sale_cash_in","cash_recv_operate_in","cash_pay_operate_out",
        "cash_pay_operate_net","cash_operate_net","cash_invest_net",
        "cash_finance_net","cash_add","cash_begin","cash_end"}, ...]``
        （金额单位元、``float`` 或 ``None``）
        """
        return self._fetch_alias(
            symbol, "cash_flow",
            report_date=report_date, size=size,
            all_pages=all_pages, max_pages=max_pages, raw=raw,
        )
