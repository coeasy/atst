# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""天天基金移动端扩展源：排行 / 批量快照 / 净值 / 基金画像。

对标 ``fundmobapi.eastmoney.com/FundMNewApi/*``，补齐
:mod:`tstdx.web.efinance_fund`（对标 efinance 的 7 个端点）与
:mod:`tstdx.web.adapters_fund`（净值 / 估值 / 列表）之外的基金域缺口。

===========================  ==================================================
上游端点                       本模块方法
===========================  ==================================================
``FundMNRank``               :meth:`FundMobRankSource.fetch_rank`
``FundMNFInfo``              :meth:`FundMobRankSource.fetch_snapshot`
``FundMNHisNetList``         :meth:`FundMobRankSource.fetch_nav_history`
``FundMNDetailInformation``  :meth:`FundMobRankSource.fetch_detail`
``FundGradeDetail``          :meth:`FundMobRankSource.fetch_rating`
``FundVPageAcc``             :meth:`FundMobRankSource.fetch_yield_curve`
``FundRankDiagram``          :meth:`FundMobRankSource.fetch_rank_trend`
===========================  ==================================================

.. note::
   ``FundMNFInfo`` 是已下线的 ``fundgz.1234567.com.cn`` 实时估值接口的官方
   替代路径：返回 ``GSZ``（估算净值）/ ``GSZZL``（估算涨跌%）/ ``GZTIME``
   （估算时间），且支持多代码逗号分隔**一次批量拉取**（旧接口只能单只请求）。

字段契约
--------
* 收益率 / 净值 / 规模：百分数或原值（``50.0`` 即 50.00%），不做额外换算。
* 缺失字段统一归 ``0.0`` / ``0`` / ``""``（与 :mod:`tstdx.web.efinance_fund`
  一致，基于 :func:`~tstdx.web.base.num_f` / :func:`num_i`）。
* 解析失败统一抛 :class:`~tstdx.errors.SourceDeprecated` 触发下线检测。

.. warning::
   ``FundGradeDetail`` / ``FundVPageAcc`` / ``FundRankDiagram`` /
   ``FundMNDetailInformation`` 为非 ``FundMN*`` 前缀端点，host 与 PascalCase
   命名沿用移动端惯例，属 best-effort：若线上返回 ``ErrCode!=0`` 请重新
   抓包校准（同 :class:`~tstdx.web.corporate.EastmoneyIpoSource` 策略）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import _mob_fund as _m
from .base import BaseWebSource, num_i
from .sources import FUND

__all__ = ["FundMobRankSource", "SORT_COLUMNS"]

#: 排行行字段映射。
_RANK_FIELDS: dict[str, tuple[str, str]] = {
    "code": ("FCODE", "s"),
    "name": ("SHORTNAME", "s"),
    "company": ("JJGS", "s"),
    "fund_type": ("FTYPE", "s"),
    "establish_date": ("ESTABDATE", "s"),
    "day_pct": ("RZDF", "f"),
    "unit_nav": ("DWJZ", "f"),
    "accum_nav": ("LJJZ", "f"),
    "return_1w": ("SYL_W", "f"),
    "return_1m": ("SYL_M", "f"),
    "return_3m": ("SYL_Q", "f"),
    "return_6m": ("SYL_6Y", "f"),
    "return_1y": ("SYL_1N", "f"),
    "return_2y": ("SYL_2N", "f"),
    "return_3y": ("SYL_3N", "f"),
    "return_total": ("SYL_Z", "f"),
    "scale": ("RLEVEL_SZ", "f"),
    "risk_level": ("RISKLEVEL", "s"),
}

#: 批量快照行字段映射。
_SNAP_FIELDS: dict[str, tuple[str, str]] = {
    "code": ("FCODE", "s"),
    "name": ("SHORTNAME", "s"),
    "nav_date": ("PDATE", "s"),
    "unit_nav": ("NAV", "f"),
    "accum_nav": ("ACCNAV", "f"),
    "nav_pct": ("NAVCHGRT", "f"),
    "est_nav": ("GSZ", "f"),
    "est_pct": ("GSZZL", "f"),
    "est_time": ("GZTIME", "s"),
    "latest_price": ("NEWPRICE", "s"),
    "price_pct": ("CHANGERATIO", "s"),
    "has_redpacket": ("ISHAVEREDPACKET", "s"),
}

#: 移动端历史净值行字段映射。
_NAV_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("FSRQ", "s"),
    "unit_nav": ("DWJZ", "f"),
    "pct_change": ("JZZZL", "f"),
    "accum_nav": ("LJJZ", "f"),
    "nav_type": ("NAVTYPE", "s"),
    "rate": ("RATE", "f"),
    "cum_return": ("SYI", "f"),
}

#: 评级行字段映射。
_RATING_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("RDATE", "s"),
    "rating_ht": ("HTPJ", "s"),
    "rating_zs": ("ZSPJ", "s"),
    "rating_sz3": ("SZPJ3", "s"),
    "rating_ja": ("JAPJ", "s"),
}

#: 累计收益走势行字段映射。
_YIELD_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("PDATE", "s"),
    "fund_yield": ("YIELD", "f"),
    "index_yield": ("INDEXYIELD", "f"),
    "peer_yield": ("FUNDTYPEYIELD", "f"),
    "benchmark_quote": ("BENCHQUOTE", "s"),
}

#: 同类排名走势行字段映射。
_RANK_TREND_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("PDATE", "s"),
    "rank": ("QRANK", "i"),
    "total": ("QSC", "i"),
}

#: ``FundMNRank`` 常用排序列。
SORT_COLUMNS = {
    "RDZF": "日涨幅",
    "DWJZ": "最新净值",
    "LJJZ": "累计净值",
    "RZDF": "日涨幅(净值)",
    "SYL_W": "近1周",
    "SYL_M": "近1月",
    "SYL_Q": "近3月",
    "SYL_6Y": "近6月",
    "SYL_1N": "近1年",
    "SYL_2N": "近2年",
    "SYL_3N": "近3年",
    "SYL_Z": "成立至今",
}


class FundMobRankSource(BaseWebSource):
    """天天基金排行 / 快照 / 净值 / 画像数据源。

    Quick start::

        from tstdx.web.fund_rank import FundMobRankSource
        src = FundMobRankSource()
        page   = src.fetch_rank(sort_column="SYL_1N", size=20)  # -> dict
        snaps  = src.fetch_snapshot(["161725", "005919"])        # -> list[dict]
        nav    = src.fetch_nav_history("161725")                 # -> list[dict]
        detail = src.fetch_detail("161725")                      # -> dict
        rate   = src.fetch_rating("161725")                      # -> list[dict]
        curve  = src.fetch_yield_curve("161725", "000300")       # -> list[dict]
        trend  = src.fetch_rank_trend("161725")                  # -> list[dict]
        src.close()
    """

    BASE = _m.MOB_BASE
    encoding = "utf-8"

    def __init__(self, **kwargs: Any) -> None:
        headers = dict(kwargs.pop("headers", None) or {})
        for k, v in _m.mob_headers().items():
            headers.setdefault(k, v)
        super().__init__(headers=headers, **kwargs)

    @property
    def source_name(self) -> str:
        return FUND

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("FundMobRankSource 为数据型源，请使用 fetch_* 方法")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _json(self, path: str) -> dict[str, Any]:
        return _m.mob_get_json(self._request_text, path)

    # -- 基金排行 ----------------------------------------------------------- #
    def fetch_rank(
        self,
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
        """基金排行榜（按类型 / 涨幅 / 净值排序，支持公司与主题过滤）。

        Parameters
        ----------
        fund_type:
            基金类型编号（``0``=全部，``25``=股票，``27``=混合，``35``=货币，
            ``6``=QDII，``4``=LOF）。
        sort_column:
            排序列，见 :data:`SORT_COLUMNS`（默认近1年）。
        sort:
            ``"desc"`` / ``"asc"``。
        page, size:
            页码（1 起）与每页条数。
        company_id:
            基金公司 id（来自 ``fund_companies``），空表示不限。
        topic:
            主题代码（来自 ``fund_themes``），空表示不限。
        risk_level:
            风险等级过滤，空表示不限。
        **extra:
            透传其他过滤参数（``BUY`` / ``DISCOUNT`` / ``ESTABDATE`` /
            ``ENDNAV`` / ``RLEVEL_SZ`` / ``ISABNORMAL`` / ``CLTYPE`` /
            ``DataConstraintType``）。

        Returns
        -------
        ``{"total": int, "page": int, "size": int, "rows": list[dict]}``；
        ``rows`` 字段见 :data:`_RANK_FIELDS`。
        """
        q = (
            f"FundType={fund_type}&SortColumn={sort_column}&Sort={sort}"
            f"&pageIndex={page}&pageSize={size}{_m.MOB_COMMON}"
        )
        if company_id:
            q += f"&CompanyId={company_id}"
        if topic:
            q += f"&TOPICAL={topic}"
        if risk_level:
            q += f"&RISKLEVEL={risk_level}"
        q += "&DataConstraintType=0&LevelTwo="
        for k, v in extra.items():
            q += f"&{k}={v}"
        payload = self._json(f"FundMNRank?{q}")
        return {
            "total": num_i(payload.get("TotalCount")),
            "page": page,
            "size": size,
            "rows": [_m.apply_fields(r, _RANK_FIELDS) for r in _m.mob_rows(payload)],
        }

    # -- 批量实时快照 ------------------------------------------------------- #
    def fetch_snapshot(self, codes: Sequence[str] | str) -> list[dict[str, Any]]:
        """批量基金实时快照（净值 + 盘中估值），一次请求多只。

        已下线的 ``fundgz`` 实时估值接口的官方替代路径。

        Parameters
        ----------
        codes:
            基金代码 list（或逗号分隔字符串）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_SNAP_FIELDS`（``est_nav`` / ``est_pct``
        / ``est_time`` 即盘中估算净值 / 估算涨跌% / 估算时间）。
        """
        raw = codes if isinstance(codes, str) else ",".join(codes)
        q = (
            f"Fcodes={raw}&plat=Iphone&product=EFund&appType=ttjj"
            f"&Version=6.3.8&deviceid={_m.DEVICE}"
        )
        payload = self._json(f"FundMNFInfo?{q}")
        return [_m.apply_fields(r, _SNAP_FIELDS) for r in _m.mob_rows(payload)]

    # -- 移动端历史净值 ----------------------------------------------------- #
    def fetch_nav_history(
        self, code: str, *, page: int = 1, size: int = 49
    ) -> list[dict[str, Any]]:
        """移动端历史净值（字段比 ``lsjz`` 更全：含 NAVTYPE / RATE / SYI）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_NAV_FIELDS`。
        """
        q = (
            f"FCODE={code}&pageIndex={page}&pageSize={size}"
            f"&deviceid={_m.DEVICE}&version=6.3.8&plat=Iphone"
        )
        payload = self._json(f"FundMNHisNetList?{q}")
        return [_m.apply_fields(r, _NAV_FIELDS) for r in _m.mob_rows(payload)]

    # -- 基金详情 ----------------------------------------------------------- #
    def fetch_detail(self, code: str) -> dict[str, Any]:
        """基金详情（风险等级 / 业绩基准 / 投资策略 / 费用）。

        Returns
        -------
        ``{"code","name","fullname","fund_type","establish_date","net_nav",
        "end_nav","risk_scale","risk_level","benchmark","index_code",
        "index_name","invest_target","invest_strategy","company","company_id",
        "manager","management_exp","trust_exp","sales_exp","preservation_type",
        "preservation_date","cycle"}``
        """
        payload = self._json(f"FundMNDetailInformation?FCODE={code}{_m.MOB_COMMON}")
        d = payload.get("Datas")
        if not isinstance(d, dict):
            d = {}
        return {
            "code": _m.s(d.get("FCODE")) or code,
            "name": _m.s(d.get("SHORTNAME")),
            "fullname": _m.s(d.get("FULLNAME")),
            "fund_type": _m.s(d.get("FTYPE")),
            "establish_date": _m.s(d.get("ESTABDATE")),
            "net_nav": _m.apply_fields(d, {"net_nav": ("NETNAV", "f")})["net_nav"],
            "end_nav": _m.s(d.get("ENDNAV")),
            "risk_scale": _m.s(d.get("RLEVEL_SZ")),
            "risk_level": _m.s(d.get("RISKLEVEL")),
            "benchmark": _m.s(d.get("BENCH")),
            "index_code": _m.s(d.get("INDEXCODE")),
            "index_name": _m.s(d.get("INDEXNAME")),
            "invest_target": _m.s(d.get("INVTGT")),
            "invest_strategy": _m.s(d.get("INVSTRA")),
            "company": _m.s(d.get("JJGS")),
            "company_id": _m.s(d.get("JJGSID")),
            "manager": _m.s(d.get("JJJL")),
            "management_exp": _m.s(d.get("MGREXP")),
            "trust_exp": _m.s(d.get("TRUSTEXP")),
            "sales_exp": _m.s(d.get("SALESEXP")),
            "preservation_type": _m.s(d.get("PRSVTYPE")),
            "preservation_date": _m.s(d.get("PRSVDATE")),
            "cycle": _m.s(d.get("CYCLE")),
        }

    # -- 基金评级 ----------------------------------------------------------- #
    def fetch_rating(self, code: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """基金历史评级（天天基金 / 招商 / 上证 / 嘉实等机构评级）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_RATING_FIELDS`。
        """
        q = f"FCODE={code}&pageIndex={page}&pageSize={size}{_m.MOB_COMMON}"
        payload = self._json(f"FundGradeDetail?{q}")
        return [_m.apply_fields(r, _RATING_FIELDS) for r in _m.mob_rows(payload)]

    # -- 累计收益走势 ------------------------------------------------------- #
    def fetch_yield_curve(self, code: str, *, index_code: str = "000300") -> list[dict[str, Any]]:
        """累计收益走势（基金 vs 指数 vs 同类），回测与超额收益分析的基础。

        Parameters
        ----------
        index_code:
            基准指数代码（``"000300"`` 沪深300 / ``"000001"`` 上证 /
            ``"399001"`` 深成 / ``"399006"`` 创业板 / ``"000905"`` 中证500 /
            ``"399005"`` 中小板 / ``"000016"`` 上证50）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_YIELD_FIELDS`。
        """
        q = f"FCODE={code}&INDEXCODE={index_code}{_m.MOB_COMMON}"
        payload = self._json(f"FundVPageAcc?{q}")
        return [_m.apply_fields(r, _YIELD_FIELDS) for r in _m.mob_rows(payload)]

    # -- 同类排名走势 ------------------------------------------------------- #
    def fetch_rank_trend(self, code: str, *, range_: str = "n") -> list[dict[str, Any]]:
        """同类排名走势（每日同类排名与总数）。

        Parameters
        ----------
        range_:
            ``"n"`` 近阶段 / ``"y"`` 近1年 / 空=全部。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_RANK_TREND_FIELDS`。
        """
        q = f"FCODE={code}&RANGE={range_}{_m.MOB_COMMON}"
        payload = self._json(f"FundRankDiagram?{q}")
        return [_m.apply_fields(r, _RANK_TREND_FIELDS) for r in _m.mob_rows(payload)]
