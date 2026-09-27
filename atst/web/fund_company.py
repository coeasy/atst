# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""天天基金公司 / 搜索数据源。

覆盖 atst 此前完全缺失的两个域：**基金公司维度**（公司列表 / 公司概况 /
旗下基金 / 规模变动 / 公司画像）与**基金搜索**（按名称 / 代码模糊搜索）。

===========================  ==================================================
上游端点                          本模块方法
===========================  ==================================================
``FundMApi/FundCompanyBaseList`` :meth:`FundCompanySource.fetch_companies`
``CompanyApi2?companyarchives``  :meth:`FundCompanySource.fetch_archives`
``CompanyApi2?fundlist``         :meth:`FundCompanySource.fetch_funds`
``CompanyApi2?companygmbd``      :meth:`FundCompanySource.fetch_scale_change`
``CompanyApi2?fundcompanybaseinfo`` :meth:`FundCompanySource.fetch_base_info`
``fundts/.../fundinfobynohigh``  :meth:`FundCompanySource.search_funds`
===========================  ==================================================

.. note::
   公司端点响应容器键为 ``data``（非移动端常规的 ``Datas``），且整体包一层
   ``{"code": 0, "data": ...}``；本模块已统一处理。

.. warning::
   ``CompanyApi2`` 的 host 与 PascalCase 命名沿用移动端惯例，属 best-effort：
   若线上返回 ``code!=0`` 请重新抓包校准。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import _mob_fund as _m
from .base import BaseWebSource
from .sources import FUND

__all__ = ["FundCompanySource"]

_FUND_COMPANY_API = "https://fundmobapi.eastmoney.com/FundMApi"
_SEARCH = "https://fundts.eastmoney.com/search/s"

#: 基金公司行字段映射（``FundCompanyBaseList`` 容器键不确定，已双键兼容）。
_COMPANY_FIELDS: dict[str, tuple[str, str]] = {
    "company_id": ("JJGSID", "s"),
    "name": ("JJGS", "s"),
    "name_abbrev": ("GSJJBName", "s"),
    "pinyin": ("JJGSJP", "s"),
    "company_code": ("GSJJBID", "s"),
    "fund_count": ("QXJJ", "s"),
}

#: 公司概况字段映射（单条 ``data`` 对象）。
_ARCHIVES_FIELDS: dict[str, tuple[str, str]] = {
    "company_name": ("FDMC", "s"),
    "establish_date": ("CLRQ", "s"),
    "total_assets": ("ZCZB", "s"),
    "first_fund_date": ("FRDB", "s"),
    "manager": ("Manager", "s"),
    "address": ("ZCDZ", "s"),
    "website": ("BGDZ", "s"),
    "contact": ("KFRX", "s"),
    "fund_count": ("Count", "i"),
    "manager_count": ("ManagerCount", "i"),
    "level": ("Level", "s"),
}

#: 公司旗下基金行字段映射。
_FUND_FIELDS: dict[str, tuple[str, str]] = {
    "code": ("FCODE", "s"),
    "feature": ("FEATURE", "s"),
    "fund_type": ("FUNDTYPE", "s"),
    "fullname": ("FULLNAME", "s"),
    "name": ("SHORTNAME", "s"),
    "company_id": ("JJGSID", "s"),
    "unit_nav": ("DWJZ", "f"),
    "accum_nav": ("LJJZ", "f"),
    "fee_1y": ("FTYI", "f"),
    "fee_2y": ("TEYI", "f"),
    "fee_3y": ("TFYI", "f"),
    "return_1d": ("SYL_D", "f"),
    "return_total": ("SYL_Z", "f"),
    "return_1y": ("SYL_Y", "f"),
    "return_2y": ("SYL_2N", "f"),
    "return_3y": ("SYL_3N", "f"),
    "return_6y": ("SYL_6Y", "f"),
    "return_jn": ("SYL_JN", "f"),
    "return_ln": ("SYL_LN", "f"),
}

#: 公司规模变动行字段映射。
_SCALE_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("FSRQ", "s"),
    "share_total": ("QMZFE", "f"),
    "nav_total": ("QMJZC", "f"),
}

#: 搜索行字段映射。
_SEARCH_FIELDS: dict[str, tuple[str, str]] = {
    "code": ("fcode", "s"),
    "show_code": ("showfcode", "s"),
    "fund_type": ("ftype", "s"),
    "name": ("shortname", "s"),
    "highlight": ("hightlight", "s"),
    "code_type": ("fcodetype", "s"),
    "second_type": ("secondfcodetype", "s"),
    "abbname": ("abbname", "s"),
    "abb_tname": ("abbtname", "s"),
    "foreshortname": ("foreshortname", "s"),
    "exchange": ("newtexch", "s"),
}


class FundCompanySource(BaseWebSource):
    """天天基金公司 / 搜索数据源。

    Quick start::

        from atst.web.fund_company import FundCompanySource
        src = FundCompanySource()
        cos    = src.fetch_companies()                          # -> list[dict]
        cc     = cos[0]["company_id"]
        arch   = src.fetch_archives(cc)                         # -> dict
        base   = src.fetch_base_info(cc)                        # -> dict
        funds  = src.fetch_funds(cc, size=50)                   # -> list[dict]
        scale  = src.fetch_scale_change(cc)                     # -> list[dict]
        hits   = src.search_funds("易方达", size=10)             # -> dict
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
        raise NotImplementedError("FundCompanySource 为数据型源，请使用 fetch_* 方法")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _json(self, path: str, *, base: str | None = None) -> dict[str, Any]:
        return _m.mob_get_json(self._request_text, path, base=base)

    # -- 基金公司列表 ------------------------------------------------------- #
    def fetch_companies(self) -> list[dict[str, Any]]:
        """全部基金公司列表（约 160 家，一次拉全）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_COMPANY_FIELDS`。
        """
        payload = self._json("FundCompanyBaseList.ashx", base=_FUND_COMPANY_API)
        rows = _m.mob_rows_any(payload, "Datas", "data", "Data", "Result")
        return [_m.apply_fields(r, _COMPANY_FIELDS) for r in rows]

    # -- 公司概况 ----------------------------------------------------------- #
    def fetch_archives(self, company_id: str) -> dict[str, Any]:
        """基金公司概况（成立时间 / 资产总规模 / 注册地址 / 官网 / 人数）。

        Parameters
        ----------
        company_id:
            公司 id（来自 :meth:`fetch_companies` 的 ``company_id``）。

        Returns
        -------
        ``dict``，字段见 :data:`_ARCHIVES_FIELDS`；无数据返回空键 dict。
        """
        q = f"cc={company_id}&action=companyarchives{_m.MOB_COMMON}"
        payload = self._json(f"CompanyApi2?{q}")
        d = payload.get("data")
        if not isinstance(d, dict):
            d = {}
        out = _m.apply_fields(d, _ARCHIVES_FIELDS)
        out["company_id"] = company_id
        return out

    # -- 公司旗下基金 ------------------------------------------------------- #
    def fetch_funds(
        self,
        company_id: str,
        *,
        fund_type: str = "all",
        page: int = 1,
        size: int = 50,
        sort_field: str = "DWJZ",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        """基金公司旗下基金列表（含各类区间收益）。

        Parameters
        ----------
        company_id:
            公司 id。
        fund_type:
            ``"all"`` 或类型编号（``1``/``2``/``3``/``4``/``5``/``7``/``8``）。
        sort_field:
            排序字段（``DWJZ`` 单位净值 / ``SYL_Y`` 近1年 / ``SYL_Z`` 成立至今）。
        sort_dir:
            ``"desc"`` / ``"asc"``。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_FUND_FIELDS`。
        """
        q = (
            f"cc={company_id}&action=fundlist&fundtype={fund_type}"
            f"&pi={page}&ps={size}&sd={sort_dir}&sf={sort_field}{_m.MOB_COMMON}"
        )
        payload = self._json(f"CompanyApi2?{q}")
        rows = _m.mob_rows_any(payload, "data", "Datas")
        return [_m.apply_fields(r, _FUND_FIELDS) for r in rows]

    # -- 公司规模变动 ------------------------------------------------------- #
    def fetch_scale_change(
        self,
        company_id: str,
        *,
        page: int = 1,
        size: int = 20,
        sort_field: str = "FSRQ",
        sort_dir: str = "desc",
    ) -> list[dict[str, Any]]:
        """基金公司旗下基金总规模变动（份额 / 净值资产，按报告期）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_SCALE_FIELDS`。
        """
        q = (
            f"cc={company_id}&action=companygmbd&pi={page}&ps={size}"
            f"&sd={sort_dir}&sf={sort_field}{_m.MOB_COMMON}"
        )
        payload = self._json(f"CompanyApi2?{q}")
        d = payload.get("data")
        if not isinstance(d, dict):
            d = {}
        rows = _m.mob_rows(d, "Datas") or _m.mob_rows_any(payload, "data")
        return [_m.apply_fields(r, _SCALE_FIELDS) for r in rows]

    # -- 公司画像（旗下基金分类 + 主题） ------------------------------------- #
    def fetch_base_info(self, company_id: str) -> dict[str, Any]:
        """基金公司画像（旗下基金分类统计 + 主题热度）。

        Returns
        -------
        ``{"company_id", "name", "name_abbrev", "fund_count",
        "manager_count", "max_yield", "fund_types": list[dict],
        "topics": list[dict]}``
        """
        q = f"cc={company_id}&action=fundcompanybaseinfo{_m.MOB_COMMON}"
        payload = self._json(f"CompanyApi2?{q}")
        d = payload.get("data")
        if not isinstance(d, dict):
            d = {}
        types: list[dict[str, Any]] = []
        for t in d.get("NewFundList") or []:
            if not isinstance(t, dict):
                continue
            types.append(
                {
                    "type_name": _m.s(t.get("TypeName")),
                    "type_count": _m.apply_fields(t, {"type_count": ("TypeCount", "i")})[
                        "type_count"
                    ],
                    "fields": _m.s(t.get("Filds")),
                    "fund_list": [
                        _m.apply_fields(r, _FUND_FIELDS) for r in _m.mob_rows(t, "fundlist")
                    ],
                }
            )
        topics: list[dict[str, Any]] = []
        ct = d.get("CompanyTopic")
        if isinstance(ct, dict):
            for t in ct.get("List") or []:
                if isinstance(t, dict):
                    topics.append(
                        _m.apply_fields(
                            t,
                            {
                                "topic_id": ("JJGSID", "s"),
                                "type": ("TTYPE", "s"),
                                "type_name": ("TTYPENAME", "s"),
                                "date": ("PDATE", "s"),
                                "ratio_1w": ("W", "f"),
                                "ratio_1m": ("M", "f"),
                                "ratio_3m": ("Q", "f"),
                                "ratio_1y": ("Y", "f"),
                            },
                        )
                    )
        return {
            "company_id": company_id,
            "name": _m.s(d.get("FDMC")),
            "name_abbrev": _m.s(d.get("GSJJBName")),
            "fund_count": _m.apply_fields(d, {"fund_count": ("Count", "i")})["fund_count"],
            "manager_count": _m.apply_fields(d, {"manager_count": ("ManagerCount", "i")})[
                "manager_count"
            ],
            "max_yield": _m.apply_fields(d, {"max_yield": ("fundmaxsyl", "f")})["max_yield"],
            "fund_types": types,
            "topics": topics,
        }

    # -- 基金搜索 ----------------------------------------------------------- #
    def search_funds(
        self,
        key: str,
        *,
        order_type: int = 2,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]:
        """按名称 / 代码模糊搜索基金。

        Parameters
        ----------
        key:
            搜索关键字（基金简称 / 代码片段）。
        order_type:
            ``2`` 默认 / ``1`` 按热度。

        Returns
        -------
        ``{"total": int, "page": int, "size": int, "rows": list[dict]}``；
        ``rows`` 字段见 :data:`_SEARCH_FIELDS`。
        """
        q = f"?orderType={order_type}&key={key}&pageindex={page}&pagesize={size}"
        payload = self._json(f"fundinfobynohigh{q}", base=_SEARCH)
        rows = _m.mob_rows_any(payload, "data", "Data", "Datas")
        return {
            "total": _m.apply_fields(payload, {"total": ("totalCount", "i")})["total"],
            "page": page,
            "size": size,
            "rows": [_m.apply_fields(r, _SEARCH_FIELDS) for r in rows],
        }
