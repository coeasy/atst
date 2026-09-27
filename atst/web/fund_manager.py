# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""天天基金基金经理数据源（移动端 JSON，替代 ``fundf10`` HTML 解析）。

:mod:`atst.web.efinance_fund` 的 :meth:`fetch_manager` 依赖
``fundf10.eastmoney.com/jjjl_{code}.html`` 的 HTML 结构做 best-effort 正则
解析——页面改版即静默失效。本源改走移动端 JSON 端点，字段稳定且信息更全：

===========================  ==================================================
上游端点                       本模块方法
===========================  ==================================================
``FundMNMangerList``         :meth:`FundManagerSource.fetch_list`
``FundMSNMangerInfo``        :meth:`FundManagerSource.fetch_profile`
``FundMSNMangerAcc``         :meth:`FundManagerSource.fetch_yield`
``FundMSNMangerPerEval``     :meth:`FundManagerSource.fetch_eval`
``FundMSNMangerPosMark``     :meth:`FundManagerSource.fetch_style`
===========================  ==================================================

.. note::
   东财端点名沿用其**原始拼写** ``Manger``（非 ``Manager``）——上游历史遗留，
   改错即 404，勿"纠正"。

字段契约
--------
* ``fetch_list`` 返回现任 + 离任全部经理；用 ``is_in_office`` 字段区分
  （``"1"``/``true`` 为现任）。
* ``fetch_eval`` 的夏普 / 最大回撤 / 胜率 / 波动率是量化选经理的核心指标，
  ``*_1y`` / ``*_3y`` 分别为近1年 / 近3年口径。
* 解析失败统一抛 :class:`~atst.errors.SourceDeprecated`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import _mob_fund as _m
from .base import BaseWebSource
from .sources import FUND

__all__ = ["FundManagerSource"]

#: 基金经理列表行字段映射。
_LIST_FIELDS: dict[str, tuple[str, str]] = {
    "mgrid": ("MGRID", "s"),
    "name": ("MGRNAME", "s"),
    "fund_code": ("FCODE", "s"),
    "days": ("DAYS", "i"),
    "start_date": ("FEMPDATE", "s"),
    "end_date": ("LEMPDATE", "s"),
    "nav_growth": ("PENAVGROWTH", "f"),
    "is_in_office": ("ISINOFFICE", "s"),
}

#: 经理业绩走势行字段映射。
_YIELD_FIELDS: dict[str, tuple[str, str]] = {
    "date": ("PDATE", "s"),
    "yield_": ("SYI", "f"),
    "avg_yield": ("AVGSYI", "f"),
    "index_yield": ("INDEXSYI", "f"),
}

#: 经理档案字段映射（单条 Datas 对象）。
_PROFILE_FIELDS: dict[str, tuple[str, str]] = {
    "mgrid": ("MGRID", "s"),
    "name": ("MGRNAME", "s"),
    "company": ("JJGS", "s"),
    "company_id": ("JJGSID", "s"),
    "sex": ("SEX", "s"),
    "resume": ("RESUME", "s"),
    "invest_method": ("INVESTMENTMETHOD", "s"),
    "invest_idea": ("INVESTMENTIDEAR", "s"),
    "total_days": ("TOTALDAYS", "i"),
    "net_nav": ("NETNAV", "f"),
    "fund_count": ("FCOUNT", "i"),
    "total_count": ("TCOUNT", "i"),
    "prev_code": ("PRECODE", "s"),
    "prev_name": ("PRENAME", "s"),
    "award_num": ("AWARDNUM", "i"),
    "award_num_3y": ("AWARDNUM_JN", "i"),
    "award_num_mixed": ("AWARDNUM_MX", "i"),
    "award_fund_num": ("AWARDFNUM", "i"),
    "max_nav_growth": ("MAXPENAVGROWTH", "f"),
    "mft_type": ("MFTYPE", "s"),
    "fund_code": ("FCODE", "s"),
    "fund_name": ("SHORTNAME", "s"),
    "max_retra_1y": ("MAXRETRA1", "f"),
    "max_earn_1y": ("MAXEARN1", "f"),
    "start_date": ("SDAY", "s"),
    "photo": ("NEWPHOTOURL", "s"),
}

#: 经理业绩评价字段映射（单条 Datas 对象）。
_EVAL_FIELDS: dict[str, tuple[str, str]] = {
    "max_ret_1y": ("MAXRETRA_1", "f"),
    "max_ret_3y": ("MAXRETRA_3", "f"),
    "hc_pct_1y": ("HCPCT_1", "f"),
    "hc_pct_3y": ("HCPCT_3", "f"),
    "sharp_1y": ("SHARP_1", "f"),
    "sharp_3y": ("SHARP_3", "f"),
    "xp_pct_1y": ("XPPCT_1", "f"),
    "xp_pct_3y": ("XPPCT_3", "f"),
    "stddev_1y": ("STDDEV_1", "f"),
    "stddev_3y": ("STDDEV_3", "f"),
    "bd_pct_1y": ("BDPCT_1", "f"),
    "bd_pct_3y": ("BDPCT_3", "f"),
    "win_1y": ("WIN_1", "i"),
    "win_3y": ("WIN_3", "i"),
    "win_pct_1y": ("WINPCT_1", "f"),
    "win_pct_3y": ("WINPCT_3", "f"),
}

#: 经理持仓行字段映射。
_POS_FIELDS: dict[str, tuple[str, str]] = {
    "code": ("GPDM", "s"),
    "name": ("GPJC", "s"),
    "exchange": ("NEWTEXCH", "s"),
    "ratio": ("JZBL", "f"),
    "index_name": ("INDEXNAME", "s"),
    "index_code": ("INDEXCODE", "s"),
    "pct_change": ("PCTNVCHG", "s"),
}

#: 经理子风格行字段映射。
_SUBSTYLE_FIELDS: dict[str, tuple[str, str]] = {
    "name": ("DLMC", "s"),
    "ratio": ("CCBL", "f"),
    "avg_ratio": ("AVRBL", "f"),
}


class FundManagerSource(BaseWebSource):
    """天天基金基金经理数据源（移动端 JSON）。

    Quick start::

        from atst.web.fund_manager import FundManagerSource
        src = FundManagerSource()
        mgrs   = src.fetch_list("161725")               # -> list[dict]
        mgr_id = mgrs[0]["mgrid"]
        profile = src.fetch_profile(mgr_id)             # -> dict
        eval_   = src.fetch_eval(mgr_id)                # -> dict
        curve   = src.fetch_yield(mgr_id)               # -> list[dict]
        style   = src.fetch_style(mgr_id)               # -> dict
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
        raise NotImplementedError("FundManagerSource 为数据型源，请使用 fetch_* 方法")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _json(self, path: str) -> dict[str, Any]:
        return _m.mob_get_json(self._request_text, path)

    # -- 基金经理列表 ------------------------------------------------------- #
    def fetch_list(self, code: str) -> list[dict[str, Any]]:
        """基金的基金经理列表（现任 + 离任）。

        稳定 JSON 版，替代 :meth:`FundMobSource.fetch_manager`
        （:mod:`atst.web.efinance_fund`）的 HTML 正则解析。

        Parameters
        ----------
        code:
            6 位基金代码（如 ``"161725"``）。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_LIST_FIELDS`；按 ``is_in_office``
        过滤现任，按 ``end_date`` 是否为空亦可判断。
        """
        payload = self._json(f"FundMNMangerList?FCODE={code}{_m.MOB_COMMON}")
        return [_m.apply_fields(r, _LIST_FIELDS) for r in _m.mob_rows(payload)]

    # -- 经理档案 ----------------------------------------------------------- #
    def fetch_profile(self, mgrid: str) -> dict[str, Any]:
        """基金经理档案（简历 / 投资理念 / 任职基金 / 获奖）。

        Parameters
        ----------
        mgrid:
            经理 id（来自 :meth:`fetch_list` 的 ``mgrid`` 字段）。

        Returns
        -------
        ``dict``，字段见 :data:`_PROFILE_FIELDS`；无数据返回空键 dict。
        """
        payload = self._json(f"FundMSNMangerInfo?FCODE={mgrid}{_m.MOB_COMMON}")
        d = payload.get("Datas")
        if not isinstance(d, dict):
            d = {}
        out = _m.apply_fields(d, _PROFILE_FIELDS)
        out["mgrid"] = out.get("mgrid") or mgrid
        return out

    # -- 经理业绩走势 ------------------------------------------------------- #
    def fetch_yield(self, mgrid: str, *, range_: str = "y") -> list[dict[str, Any]]:
        """基金经理业绩走势（任职以来累计收益 vs 同类 vs 指数）。

        Parameters
        ----------
        range_:
            ``"n"`` 近阶段 / ``"y"`` 近1年 / ``"ln"`` 任职以来。

        Returns
        -------
        ``list[dict]``，字段见 :data:`_YIELD_FIELDS`。
        """
        q = f"mGRID={mgrid}&rANGE={range_}{_m.MOB_COMMON}"
        payload = self._json(f"FundMSNMangerAcc?{q}")
        return [_m.apply_fields(r, _YIELD_FIELDS) for r in _m.mob_rows(payload)]

    # -- 经理业绩评价 ------------------------------------------------------- #
    def fetch_eval(self, mgrid: str) -> dict[str, Any]:
        """基金经理业绩评价（夏普 / 最大回撤 / 胜率 / 波动率 / 超额）。

        量化筛选基金经理的核心接口：``sharp_1y`` / ``max_ret_1y`` /
        ``win_pct_1y`` / ``stddev_1y`` 可直接构成经理打分卡。

        Parameters
        ----------
        mgrid:
            经理 id。

        Returns
        -------
        ``dict``，字段见 :data:`_EVAL_FIELDS`；无数据返回空键 dict。
        """
        payload = self._json(f"FundMSNMangerPerEval?mGRID={mgrid}{_m.MOB_COMMON}")
        d = payload.get("Datas")
        if not isinstance(d, dict):
            d = {}
        out = _m.apply_fields(d, _EVAL_FIELDS)
        out["mgrid"] = mgrid
        return out

    # -- 经理风格 / 持仓 ---------------------------------------------------- #
    def fetch_style(self, mgrid: str) -> dict[str, Any]:
        """基金经理持仓风格画像（重仓股 / 风格标签 / 子风格分布）。

        Returns
        -------
        ``{"mgrid", "pos_date", "holdings": list[dict], "style": dict,
        "sub_style": list[dict]}``；``holdings`` 字段见 :data:`_POS_FIELDS`，
        ``sub_style`` 字段见 :data:`_SUBSTYLE_FIELDS`。
        """
        payload = self._json(f"FundMSNMangerPosMark?mGRID={mgrid}{_m.MOB_COMMON}")
        d = payload.get("Datas")
        if not isinstance(d, dict):
            d = {}
        pos = _m.mob_rows(d, "Pos")
        subs = _m.mob_rows(d, "SubStyle")
        style_raw = d.get("Style")
        style = (
            _m.apply_fields(
                style_raw,
                {
                    "fund_scale": ("FSCALE", "f"),
                    "fund_style": ("FSTYLE", "s"),
                    "stock_status": ("GZQK", "s"),
                    "yield_status": ("YLQK", "s"),
                },
            )
            if isinstance(style_raw, dict)
            else {}
        )
        return {
            "mgrid": mgrid,
            "pos_date": _m.s(d.get("PosDate")),
            "holdings": [_m.apply_fields(r, _POS_FIELDS) for r in pos],
            "style": style,
            "sub_style": [_m.apply_fields(r, _SUBSTYLE_FIELDS) for r in subs],
        }
