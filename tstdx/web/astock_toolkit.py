# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""astock-data-toolkit 对标扩展（估值 / 分红 / 增减持 / 财务摘要 / 公告）。

对标 https://github.com/tiantianlaolao/astock-data-toolkit 的 Parquet/SQLite
数据域，补齐 tstdx 此前缺失的「基本面衍生」查询接口。

后端均为东财 datacenter-web 报表族（与 :mod:`tstdx.web.corporate` 同源），
沿用主机池 failover + 黑名单 + 失败计数（单主机池，行为等价）。解析失败统一
抛 :class:`~tstdx.errors.SourceDeprecated`，触发库内「下线检测」。

.. note::
   估值 / 增减持 / 财务摘要三个报表名（``RPT_VALUEASSESS_DET`` /
   ``RPT_CAPITAL_PARTICIPATION_DET`` / ``RPT_F10_FINANCE_MAIN``）为 best-effort
   映射——东财报表名偶发变动，若服务端返回「报表配置不存在 (code=9501)」需重新
   抓包校准（与 :class:`~tstdx.web._session_efinance.StockEfinanceMixin`
   的 ``ipo_review`` 同策略）。字段名采用东财常见列名 + 别名容错，未命中列
   原样透传，不丢数据。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .corporate import (
    VALID_REPORTS,
    EastmoneyDataCenterSource,
    EastmoneyNoticeSource,
)

__all__ = [
    "EastmoneyDividendSource",
    "EastmoneyValuationSource",
    "EastmoneyHolderChangeSource",
    "EastmoneyFinanceMainSource",
    "EastmoneyAnnouncementSource",
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
        if not s or s in ("-", "--", "None", "nan", "None"):
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    if v != v:  # NaN
        return None
    return v


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    """从行中取首个存在的列名对应值（别名容错）。"""
    for n in names:
        if n in row and row[n] is not None:
            return row[n]
    return None


def _s(value: Any) -> str:
    return "" if value is None else str(value)


# --------------------------------------------------------------------------- #
# 分红送转（RPT_SHAREBONUS_DET，2026-09-06 实测可用）
# --------------------------------------------------------------------------- #
class EastmoneyDividendSource(EastmoneyDataCenterSource):
    """分红送配明细（东财 ``RPT_SHAREBONUS_DET``）。

    口径约定（与东财报表一致）：送股 / 转增比例均为「每 10 股」，派息为
    「每 10 股（元）」——字段名显式标注 ``_per_10`` 避免与 tstdx 其它每股口径混淆。
    """

    JSON_LABEL = "分红送转"

    #: 列名 → 归一化键（含别名容错）
    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "report_date": ("REPORT_DATE", "END_DATE"),
        "bonus_shares_per_10": ("BONUS_SHARES_RATIO", "SGBL"),
        "transfer_shares_per_10": ("TRANSFER_SHARES_RATIO", "ZGBL"),
        "cash_dividend_per_10": ("CASH_DIVIDEND_RATIO", "XJFH"),
        "ex_dividend_date": ("EX_DIVIDEND_DATE", "CQCXR"),
        "record_date": ("RECORD_DATE", "GQDJR"),
        "dividend_date": ("DIVIDEND_DATE", "FXRQ"),
        "progress": ("PROGRESS", "IMPL_PLAN_PROGRESS", "IS_OPEN"),
    }
    #: 数值化字段（每 10 股比例），其余保留字符串
    _NUM_FIELDS = frozenset(
        {"bonus_shares_per_10", "transfer_shares_per_10", "cash_dividend_per_10"}
    )

    def fetch_dividend(self, symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """分红送转历史（按除权除息日降序）。

        Returns
        -------
        ``[{"code","name","report_date","bonus_shares_per_10",
        "transfer_shares_per_10","cash_dividend_per_10","ex_dividend_date",
        "record_date","dividend_date","progress"}, ...]``（比例为 ``float`` 或
        ``None``，日期 / 进度为字符串）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=page,
            size=size,
            report=VALID_REPORTS["dividend"],
            sort_columns="EX_DIVIDEND_DATE",
            sort_types="-1",
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            rec: dict[str, Any] = {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
            }
            for key, names in self.FIELD_MAP.items():
                val = _pick(r, *names)
                rec[key] = _num(val) if key in self._NUM_FIELDS else _s(val)
            out.append(rec)
        return out


# --------------------------------------------------------------------------- #
# 估值分析（RPT_VALUEASSESS_DET，best-effort）
# --------------------------------------------------------------------------- #
class EastmoneyValuationSource(EastmoneyDataCenterSource):
    """个股估值分析（东财 ``RPT_VALUEASSESS_DET``，best-effort）。

    返回按报告期降序的估值快照：PE(TTM) / PB / PS(TTM) / PCF(TTM) / 总股本 /
    总市值。东财报表名与列名偶发变动，未命中列原样透传。
    """

    JSON_LABEL = "估值分析"
    REPORT = "RPT_VALUEASSESS_DET"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "report_date": ("REPORT_DATE", "END_DATE", "DATE"),
        "pe_ttm": ("PE_TTM", "PE", "PE_DYNAMIC"),
        "pb": ("PB", "PB_MRQ"),
        "ps_ttm": ("PS_TTM", "PS"),
        "pcf_ttm": ("PCF_TTM", "PCF", "PCF_NCF_TTM"),
        "total_share": ("TOTAL_SHARE", "ORG_TOTAL_SHARE", "TOTAL_SHARES"),
        "total_mv": ("TOTAL_MV", "ORG_TOTAL_MV", "TOTAL_MARKET_CAP"),
    }

    def fetch_valuation(self, symbol: str) -> list[dict[str, Any]]:
        """估值快照列表（最新报告期在前）。

        Returns
        -------
        ``[{"code","name","report_date","pe_ttm","pb","ps_ttm","pcf_ttm",
        "total_share","total_mv"}, ...]``（数值为 ``float`` 或 ``None``）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=1,
            size=20,
            report=self.REPORT,
            sort_columns="REPORT_DATE",
            sort_types="-1",
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            rec: dict[str, Any] = {
                "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
            }
            for key, names in self.FIELD_MAP.items():
                val = _pick(r, *names)
                rec[key] = _num(val)
            out.append(rec)
        return out


# --------------------------------------------------------------------------- #
# 股东增减持（RPT_CAPITAL_PARTICIPATION_DET，best-effort）
# --------------------------------------------------------------------------- #
class EastmoneyHolderChangeSource(EastmoneyDataCenterSource):
    """股东 / 董监高增减持（东财 ``RPT_CAPITAL_PARTICIPATION_DET``，best-effort）。

    与工具箱「三所官方披露 API」同源但走东财聚合接口（单入口、免反爬）。
    变动方向由 ``change_shares`` 正负表达（正=增持，负=减持）。
    """

    JSON_LABEL = "增减持"
    REPORT = "RPT_CAPITAL_PARTICIPATION_DET"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "person": ("PERSON_NAME", "PERSON", "GDXM"),
        "position": ("POSITION", "ZW", "DUTY"),
        "actor": ("ACTOR_NAME", "ACTOR", "GDXM"),
        "relation": ("RELATION", "GXLX", "GXLB"),
        "change_shares": ("CHANGE_SHARES", "BDGS", "CHANGE_AMOUNT"),
        "avg_price": ("AVG_PRICE", "BDJJ", "PRICE"),
        "shares_after": ("HOLD_SHARES_AFTER", "CGZS", "NEW_AMOUNT"),
        "change_ratio": ("CHANGE_RATIO", "CGBDBL", "CHANGE_RATIO_PCT"),
        "reason": ("CHANGE_REASON", "BDYY", "REASON"),
        "change_date": ("CHANGE_DATE", "JYRQ", "CHANGE_DATE_STR"),
        "disclosure_date": ("DISCLOSURE_DATE", "FORM_DATE", "DISCLOSURE_DATE_STR"),
    }

    def fetch_holder_changes(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """增减持事件列表（按披露日期降序）。

        Returns
        -------
        ``[{"code","name","person","position","actor","relation",
        "change_shares","avg_price","shares_after","change_ratio","reason",
        "change_date","disclosure_date"}, ...]``
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=page,
            size=size,
            report=self.REPORT,
            sort_columns="DISCLOSURE_DATE",
            sort_types="-1",
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            rec: dict[str, Any] = {
                "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
            }
            for key, names in self.FIELD_MAP.items():
                val = _pick(r, *names)
                rec[key] = (
                    _num(val)
                    if key
                    not in (
                        "person",
                        "position",
                        "actor",
                        "relation",
                        "reason",
                        "change_date",
                        "disclosure_date",
                    )
                    else _s(val)
                )
            out.append(rec)
        return out


# --------------------------------------------------------------------------- #
# 财务摘要 / 主要指标（RPT_F10_FINANCE_MAIN，best-effort）
# --------------------------------------------------------------------------- #
class EastmoneyFinanceMainSource(EastmoneyDataCenterSource):
    """财务主要指标摘要（东财 ``RPT_F10_FINANCE_MAIN``，best-effort）。

    对标工具箱「财务摘要 28 指标」的东财原生精简版：每股收益 / ROE / 每股净资产 /
    营收 / 净利润 / 同比 / 毛利率 / 资产负债率。东财报表名与列名偶发变动，
    未命中列原样透传。
    """

    JSON_LABEL = "财务摘要"
    REPORT = "RPT_F10_FINANCE_MAIN"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "report_date": ("REPORT_DATE", "END_DATE", "DATE"),
        "eps": ("BASICEPS", "EPS", "EPS_DILUTED"),
        "roe": ("ROE", "WEIGHTAVGROE", "XSJZCSYL"),
        "bps": ("BVEPS", "BPS", "MGBPS"),
        "revenue": ("TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "YYSR"),
        "net_profit": ("PARENT_NETPROFIT", "NETPROFIT", "JLR"),
        "revenue_yoy": ("OPERATE_INCOME_YOY", "REVENUE_YOY", "YYSR_TB"),
        "net_profit_yoy": ("PARENT_NETPROFIT_YOY", "NETPROFIT_YOY", "JLR_TB"),
        "gross_margin": ("GROSS_MARGIN", "XSMLL", "MAOLILV"),
        "debt_ratio": ("DEBT_ASSET_RATIO", "ZCFZL", "ASSET_LIAB_RATIO"),
    }

    def fetch_main_indicators(self, symbol: str) -> list[dict[str, Any]]:
        """财务主要指标列表（按报告期降序）。

        Returns
        -------
        ``[{"code","name","report_date","eps","roe","bps","revenue",
        "net_profit","revenue_yoy","net_profit_yoy","gross_margin",
        "debt_ratio"}, ...]``（数值为 ``float`` 或 ``None``）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=1,
            size=20,
            report=self.REPORT,
            sort_columns="REPORT_DATE",
            sort_types="-1",
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            rec: dict[str, Any] = {
                "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
            }
            for key, names in self.FIELD_MAP.items():
                rec[key] = _num(_pick(r, *names))
            out.append(rec)
        return out


# --------------------------------------------------------------------------- #
# 公告（复用 corporate.EastmoneyNoticeSource，仅做命名对齐）
# --------------------------------------------------------------------------- #
class EastmoneyAnnouncementSource(EastmoneyNoticeSource):
    """上市公司公告（东财 ``np-anotice-stock``，复用 :class:`EastmoneyNoticeSource`）。"""

    JSON_LABEL = "公告"
