# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""治理 / 评级预测扩展（董监高持股 / 股东增减持 / 公司概况 / 券商评级目标价）。

补齐 tstdx 此前缺失的**公司治理**与**卖方一致预期**数据域：

* **董监高持股明细** —— 排查内部人减持 / 高位套现；
* **股东增减持** —— 大股东 / 战略投资者动向（比「十大流通股东」快照更细）；
* **公司概况** —— 法定代表人 / 董事长 / 主营 / 办公地址 / 员工数（护城河分析的
  结构化锚点）；
* **券商评级与目标价** —— 评级分布 / 覆盖机构数 / 多年度 EPS 预测 / 目标价区间
  （「估值合理性」与「市场情绪」两维度的量化入口）。

后端均为东财 ``datacenter-web`` 报表族（与 :mod:`tstdx.web.corporate` 同源），
沿用主机池 failover + 黑名单 + 失败计数。

接口事实（2026-09 抓包验证，tstdx 自有实现）::

    https://datacenter-web.eastmoney.com/api/data/v1/get
      ?reportName=RPT_EXECUTIVE_HOLD_DETAILS&columns=ALL
      &sortColumns=CHANGE_DATE&sortTypes=-1
      &pageSize=20&pageNumber=1&source=WEB&client=WEB
      &filter=(SECURITY_CODE%3D%22600519%22)

    // 成功:
    {"version":"...","result":{"pages":1,"data":[{...}],"count":3},
     "success":true,"message":"ok","code":0}
    // 失败（报表名拼错 / 已下线）:
    {"version":null,"result":null,"success":false,
     "message":"报表配置不存在,RPT_XXX","code":9501}

.. note::
   四个报表名（``RPT_EXECUTIVE_HOLD_DETAILS`` / ``RPT_SHARE_HOLDER_INCREASE`` /
   ``RPT_F10_BASIC_ORGINFO`` / ``RPT_WEB_RESPREDICT``）及**排序列**均为
   2026-09 真实抓包校准——东财报表名与列名偶发变动，若服务端返回「报表配置
   不存在 (code=9501)」或「排序列不存在」需重新抓包校准（与
   :mod:`tstdx.web.astock_toolkit` 的 ``RPT_VALUEASSESS_DET`` 同策略）。
   字段名采用真实列名 + 别名容错，未命中列原样透传，不丢数据。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .astock_toolkit import _num, _pick, _s
from .corporate import EastmoneyDataCenterSource

__all__ = [
    "EastmoneyExecutiveHoldSource",
    "EastmoneyShareholderChangeSource",
    "EastmoneyOrgProfileSource",
    "EastmoneyRatingForecastSource",
]


# --------------------------------------------------------------------------- #
# 董监高持股明细
# --------------------------------------------------------------------------- #
class EastmoneyExecutiveHoldSource(EastmoneyDataCenterSource):
    """董监高持股变动明细（东财 ``RPT_EXECUTIVE_HOLD_DETAILS``）。

    与「十大流通股东」快照不同：本表记录**每一次**董监高增减持事件，
    可用于排查内部人减持节奏与高位套现。
    """

    JSON_LABEL = "董监高持股"
    REPORT = "RPT_EXECUTIVE_HOLD_DETAILS"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "executive": ("EXECUTIVE_NAME", "HOLDER_NAME", "PERSON_NAME"),
        "position": ("POSITION", "DUTY", "ZW"),
        "change_date": ("CHANGE_DATE", "CHANGE_DATE_TIME", "NOTICE_DATE"),
        "change_shares": ("CHANGE_SHARES", "CHANGE_SHARE_NUM", "CHANGE_QUANTITY"),
        "change_price": ("CHANGE_PRICE", "CHANGE_AVG_PRICE"),
        "change_ratio": ("CHANGE_RATIO", "CHANGE_HOLD_RATIO"),
        "hold_shares": ("HOLD_SHARES", "HOLD_SHARE_NUM", "HOLD_QUANTITY"),
        "hold_ratio": ("HOLD_RATIO", "HOLD_RATIO_AFTER"),
        "relationship": ("RELATIONSHIP", "RELATIVE", "RELATED_TYPE"),
        "market": ("SECURITY_MARKET_CODE", "MARKET", "TRADE_MARKET"),
    }
    _STR_FIELDS = frozenset(
        {"executive", "position", "change_date", "relationship", "market"}
    )

    def fetch_executive_holds(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """董监高持股变动历史（按变动日期降序）。

        Returns
        -------
        ``[{"code","name","executive","position","change_date","change_shares",
        "change_price","change_ratio","hold_shares","hold_ratio","relationship",
        "market"}, ...]``（股数为股、价格为元、比例为百分数，``float`` 或 ``None``）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=page,
            size=size,
            report=self.REPORT,
            sort_columns="CHANGE_DATE",
            sort_types="-1",
        )
        return [self._normalize(r) for r in rows]

    def _normalize(self, r: Mapping[str, Any]) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
        }
        for key, names in self.FIELD_MAP.items():
            val = _pick(r, *names)
            rec[key] = _s(val) if key in self._STR_FIELDS else _num(val)
        return rec


# --------------------------------------------------------------------------- #
# 股东增减持
# --------------------------------------------------------------------------- #
class EastmoneyShareholderChangeSource(EastmoneyDataCenterSource):
    """股东增减持明细（东财 ``RPT_SHARE_HOLDER_INCREASE``）。

    与 :class:`~tstdx.web.astock_toolkit.EastmoneyHolderChangeSource`
    （``RPT_CAPITAL_PARTICIPATION_DET``，董监高增减持聚合口径）互补——
    本表聚焦**大股东 / 战略投资者 / 机构股东**的持股比例变动。

    .. note::
       真实排序列为 ``NOTICE_DATE``（公告日），非 ``CHANGE_DATE``（后者返回
       「排序列不存在」）；2026-09 抓包校准。
    """

    JSON_LABEL = "股东增减持"
    REPORT = "RPT_SHARE_HOLDER_INCREASE"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "holder": ("HOLDER_NAME", "HOLDER_NAME_ABBR", "HOLDER"),
        "holder_type": ("HOLDER_TYPE", "HOLDER_TYPE_NAME", "HOLDER_CATEGORY"),
        "change_date": ("NOTICE_DATE", "CHANGE_DATE", "CHANGE_DATE_TIME"),
        "end_date": ("END_DATE", "TRADE_DATE", "REPORT_DATE"),
        "change_shares": ("CHANGE_NUM", "CHANGE_NUM_SYMBOL", "CHANGE_SHARES"),
        "change_ratio": ("CHANGE_RATE", "AFTER_CHANGE_RATE", "CHANGE_FREE_RATIO"),
        "change_price": ("TRADE_AVERAGE_PRICE", "REAL_PRICE", "CLOSE_PRICE"),
        "hold_after": ("AFTER_HOLDER_NUM", "HOLD_AFTER", "HOLD_SHARE_AFTER"),
        "hold_ratio_after": ("HOLD_RATIO", "HOLD_RATIO_AFTER", "RATIO_AFTER"),
        "direction": ("DIRECTION", "CHANGE_TYPE", "CHANGE_DIRECTION"),
        "market": ("MARKET", "TRADE_MARKET", "SECURITY_MARKET_CODE"),
        "start_date": ("START_DATE", "BEGIN_DATE"),
    }
    _STR_FIELDS = frozenset(
        {"holder", "holder_type", "change_date", "end_date",
         "direction", "market", "start_date"}
    )

    def fetch_shareholder_changes(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> list[dict[str, Any]]:
        """股东增减持历史（按公告日期降序）。

        Returns
        -------
        ``[{"code","name","holder","holder_type","change_date","end_date",
        "change_shares","change_ratio","change_price","hold_after",
        "hold_ratio_after","direction","market","start_date"}, ...]``
        （``change_shares`` 正=增持、负=减持；价格单位元；比例为百分数）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=page,
            size=size,
            report=self.REPORT,
            sort_columns="NOTICE_DATE",
            sort_types="-1",
        )
        return [self._normalize(r) for r in rows]

    def _normalize(self, r: Mapping[str, Any]) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
        }
        for key, names in self.FIELD_MAP.items():
            val = _pick(r, *names)
            rec[key] = _s(val) if key in self._STR_FIELDS else _num(val)
        return rec


# --------------------------------------------------------------------------- #
# 公司概况
# --------------------------------------------------------------------------- #
class EastmoneyOrgProfileSource(EastmoneyDataCenterSource):
    """公司概况（东财 ``RPT_F10_BASIC_ORGINFO``）。

    护城河 / 治理分析的结构化锚点：法定代表人、董事长、主营、办公地址、
    员工数、地域、实际控制人等——这些字段无法从行情或财务表推导。

    .. note::
       报表名为 ``RPT_F10_BASIC_ORGINFO``（57 列），非
       ``RPT_F10_INFO_ORGPROFILE``（后者返回「报表配置不存在」）；
       2026-09 抓包校准。员工数列名为 ``EMP_NUM``（非 ``EMPLOYEE_NUM``）。
    """

    JSON_LABEL = "公司概况"
    REPORT = "RPT_F10_BASIC_ORGINFO"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "chairman": ("CHAIRMAN", "CHAIRMAN_NAME", "LEGAL_CHAIRMAN"),
        "legal_person": ("LEGAL_PERSON", "LEGAL_PERSON_NAME"),
        "secretary": ("SECRETARY", "SECRETARY_NAME", "BOARD_SECRETARY"),
        "president": ("PRESIDENT", "GM", "GENERAL_MANAGER"),
        "founded_date": ("FOUND_DATE", "FOUNDED_DATE", "ESTABLISH_DATE"),
        "listing_date": ("LISTING_DATE", "ISSUE_DATE"),
        "reg_address": ("REG_ADDRESS", "REGISTER_ADDRESS", "ADDRESS"),
        "address": ("ADDRESS", "REG_ADDRESS"),
        "postcode": ("ADDRESS_POSTCODE", "POSTCODE", "ZIP_CODE", "POSTAL_CODE"),
        "tel": ("ORG_TEL", "TEL", "TELEPHONE", "PHONE"),
        "fax": ("ORG_FAX", "FAX",),
        "official_site": ("ORG_WEB", "OFFICIAL_SITE", "WEB_SITE", "WEBSITE"),
        "email": ("ORG_EMAIL", "EMAIL", "E_MAIL"),
        "main_business": ("MAIN_BUSINESS", "MAIN_PRODUCT", "BUSINESS_SUMMARY"),
        "business_scope": ("BUSINESS_SCOPE", "BUSINESS_SCOPE_DESC"),
        "employee_num": ("EMP_NUM", "EMPLOYEE_NUM", "EMPLOYEE_COUNT", "STAFF_NUM"),
        "province": ("PROVINCE", "PROVINCE_NAME", "REG_PROVINCE"),
        "industry": ("INDUSTRYCSRC1", "INDUSTRY", "INDUSTRY_NAME", "EM2016"),
        "org_summary": ("ORG_PROFILE", "ORG_SUMMARY", "COMPANY_PROFILE", "ORG_ABSTRACT"),
        "org_name": ("ORG_NAME", "SECURITY_NAME", "FULL_NAME"),
        "org_name_en": ("ORG_NAME_EN", "ORG_NAME_ENGLISH"),
        "actual_holder": ("ACTUAL_HOLDER", "ACTUAL_CONTROLLER"),
        "account_firm": ("ACCOUNTFIRM_NAME", "ACCOUNTING_FIRM", "AUDIT_FIRM"),
        "law_firm": ("LAW_FIRM", "LEGAL_FIRM"),
        "reg_capital": ("REG_CAPITAL", "REGISTERED_CAPITAL", "REGISTERED_CAP"),
        "trade_market": ("TRADE_MARKET", "EXCHANGE", "TRADE_MARKET_NAME"),
        "independent_directors": ("INDEDIRECTORS", "INDEPENDENT_DIRECTORS"),
        "security_type": ("SECURITY_TYPE", "STOCK_TYPE", "SECURITY_TYPEE"),
    }
    _NUM_FIELDS = frozenset({"employee_num", "reg_capital"})

    def fetch_org_profile(self, symbol: str) -> dict[str, Any] | None:
        """公司概况快照（单只标的，取最新一条）。

        Returns
        -------
        ``{"code","name","chairman","legal_person","secretary","president",
        "founded_date","listing_date","reg_address","address","postcode",
        "tel","fax","official_site","email","main_business","business_scope",
        "employee_num","province","industry","org_summary","org_name",
        "org_name_en","actual_holder","account_firm","law_firm","reg_capital",
        "trade_market","independent_directors","security_type"} | None``
        （``employee_num`` 为人、``reg_capital`` 为万元，其余为字符串；
        无数据时 ``None``）
        """
        rows = self.fetch_rows_by_code(
            symbol,
            column="SECURITY_CODE",
            page=1,
            size=1,
            report=self.REPORT,
        )
        if not rows:
            return None
        return self._normalize(rows[0])

    def fetch_org_profiles(
        self, symbols: Sequence[str], *, size: int = 50
    ) -> list[dict[str, Any]]:
        """公司概况批量（按代码去重，保留服务端返回顺序）。"""
        from ..domain.symbol import split_symbol

        codes = [split_symbol(s)[1] for s in symbols]
        filters = [f'SECURITY_CODE="{c}"' for c in codes]
        rows = self.fetch_rows(
            filters=filters,
            page=1,
            size=size,
            report=self.REPORT,
        )
        return [self._normalize(r) for r in rows]

    def _normalize(self, r: Mapping[str, Any]) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
        }
        for key, names in self.FIELD_MAP.items():
            val = _pick(r, *names)
            rec[key] = _num(val) if key in self._NUM_FIELDS else _s(val)
        return rec


# --------------------------------------------------------------------------- #
# 券商评级与目标价
# --------------------------------------------------------------------------- #
class EastmoneyRatingForecastSource(EastmoneyDataCenterSource):
    """券商评级与目标价（东财 ``RPT_WEB_RESPREDICT``）。

    一致预期入口：**单行聚合**结构——评级分布（买入 / 增持 / 长期 / 中性 /
    减持 / 卖出）、覆盖机构数、未来四年 EPS 预测、目标价区间
    （``DEC_AIMPRICEMAX`` / ``DEC_AIMPRICEMIN``）。

    与 :meth:`tstdx.web.corporate.EastmoneyResearchSource.fetch_reports`
    （研报列表 + ``emRatingName``，逐篇明细）互补——本表为**结构化聚合数值**，
    可直接做估值分位与一致预期偏离计算。

    .. note::
       真实排序列为 ``RATING_ORG_NUM``（覆盖机构数），非 ``PUBLISH_DATE``
       （后者返回「排序列不存在」）；2026-09 抓包校准。目标价字段为
       ``DEC_AIMPRICEMAX`` / ``DEC_AIMPRICEMIN``（非 ``TARGET_PRICE``）。
    """

    JSON_LABEL = "评级预测"
    REPORT = "RPT_WEB_RESPREDICT"

    FIELD_MAP: dict[str, tuple[str, ...]] = {
        "rating_org_num": ("RATING_ORG_NUM", "ORG_NUM", "RATING_COUNT"),
        "rating_buy_num": ("RATING_BUY_NUM",),
        "rating_add_num": ("RATING_ADD_NUM",),
        "rating_long_num": ("RATING_LONG_NUM",),
        "rating_neutral_num": ("RATING_NEUTRAL_NUM",),
        "rating_reduce_num": ("RATING_REDUCE_NUM",),
        "rating_sale_num": ("RATING_SALE_NUM",),
        "year1": ("YEAR1",),
        "year2": ("YEAR2",),
        "year3": ("YEAR3",),
        "year4": ("YEAR4",),
        "year_mark1": ("YEAR_MARK1",),
        "year_mark2": ("YEAR_MARK2",),
        "year_mark3": ("YEAR_MARK3",),
        "year_mark4": ("YEAR_MARK4",),
        "eps1": ("EPS1",),
        "eps2": ("EPS2",),
        "eps3": ("EPS3",),
        "eps4": ("EPS4",),
        "target_price_max": ("DEC_AIMPRICEMAX", "TARGET_PRICE_MAX", "GOAL_PRICE_MAX"),
        "target_price_min": ("DEC_AIMPRICEMIN", "TARGET_PRICE_MIN", "GOAL_PRICE_MIN"),
        "industry_board": ("INDUSTRY_BOARD",),
        "concept_boards": ("CONCEPTINDEX_BOARD",),
        "region_board": ("REGION_BOARD",),
    }
    _STR_FIELDS = frozenset(
        {"year_mark1", "year_mark2", "year_mark3", "year_mark4",
         "industry_board", "concept_boards", "region_board"}
    )

    def fetch_rating_forecast(
        self,
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        sort_columns: str = "RATING_ORG_NUM",
    ) -> list[dict[str, Any]]:
        """券商评级与目标价（默认按覆盖机构数降序；``symbol`` 空 = 全市场最新）。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀；空串表示全市场最新评级。
        sort_columns:
            排序键；常用 ``RATING_ORG_NUM``（覆盖机构数最多）。

        Returns
        -------
        ``[{"code","name","rating_org_num","rating_buy_num","rating_add_num",
        "rating_long_num","rating_neutral_num","rating_reduce_num",
        "rating_sale_num","year1","year2","year3","year4","year_mark1",
        "year_mark2","year_mark3","year_mark4","eps1","eps2","eps3","eps4",
        "target_price_max","target_price_min","industry_board",
        "concept_boards","region_board"}, ...]``
        （EPS 单位元/股、目标价单位元、机构数为整数、``float`` 或 ``None``）
        """
        if symbol:
            rows = self.fetch_rows_by_code(
                symbol,
                column="SECURITY_CODE",
                page=page,
                size=size,
                report=self.REPORT,
                sort_columns=sort_columns,
                sort_types="-1",
            )
        else:
            rows = self.fetch_rows(
                page=page,
                size=size,
                report=self.REPORT,
                sort_columns=sort_columns,
                sort_types="-1",
            )
        return [self._normalize(r) for r in rows]

    def fetch_rating_consensus(
        self, symbol: str, *, page: int = 1, size: int = 20
    ) -> dict[str, Any] | None:
        """一致预期聚合快照（本地计算：EPS 均值 + 目标价均值 + 评级分布）。

        基于 :meth:`fetch_rating_forecast` 的结果本地聚合，不额外请求。
        数据来源为单行聚合结构（每只标的仅一条记录），故 ``sample`` 通常
        为 1。

        Returns
        -------
        ``{"code","name","sample","target_price_min","target_price_max",
        "target_price_mean","predict_eps_mean","rating_org_num",
        "rating_dist":{"买入":36,"增持":9,"长期":45}} | None``
        """
        rows = self.fetch_rating_forecast(symbol, page=page, size=size)
        if not rows:
            return None
        r = rows[0]
        # EPS 均值（仅统计非 None 年份）
        eps_values = [r[k] for k in ("eps1", "eps2", "eps3", "eps4")
                      if r.get(k) is not None]
        # 目标价均值（取区间上下限均值）
        prices = [r[k] for k in ("target_price_min", "target_price_max")
                  if r.get(k) is not None]
        # 评级分布（中文标签）
        dist: dict[str, int] = {}
        for key, label in (
            ("rating_buy_num", "买入"),
            ("rating_add_num", "增持"),
            ("rating_long_num", "长期"),
            ("rating_neutral_num", "中性"),
            ("rating_reduce_num", "减持"),
            ("rating_sale_num", "卖出"),
        ):
            val = r.get(key)
            if val is not None:
                dist[label] = int(val)
        return {
            "code": r.get("code", ""),
            "name": r.get("name", ""),
            "sample": len(rows),
            "target_price_min": r.get("target_price_min"),
            "target_price_max": r.get("target_price_max"),
            "target_price_mean": round(sum(prices) / len(prices), 2) if prices else None,
            "predict_eps_mean": round(sum(eps_values) / len(eps_values), 2)
            if eps_values else None,
            "rating_org_num": r.get("rating_org_num"),
            "rating_dist": dist,
        }

    def _normalize(self, r: Mapping[str, Any]) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "code": _s(r.get("SECURITY_CODE") or r.get("SECUCODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
        }
        for key, names in self.FIELD_MAP.items():
            val = _pick(r, *names)
            rec[key] = _s(val) if key in self._STR_FIELDS else _num(val)
        return rec
