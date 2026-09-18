# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""东财基金（天天基金移动端 ``fundmobapi``）数据适配器（efinance 基金模块对标）。

对标 ``efinance.fund`` 的全部公开能力：

====================  ==========================================
efinance 函数         本模块 / 门面方法
====================  ==========================================
``get_base_info``     :meth:`FundMobSource.fetch_base_info` / ``WebQuoteSession.fund_base_info``
``get_fund_manager``  :meth:`FundMobSource.fetch_manager` / ``WebQuoteSession.fund_manager``
``get_invest_position`` :meth:`FundMobSource.fetch_holdings` / ``WebQuoteSession.fund_holdings``
``get_period_change`` :meth:`FundMobSource.fetch_period_change` / ``WebQuoteSession.fund_period_change``
``get_types_percentage`` :meth:`FundMobSource.fetch_asset_allocation` / ``WebQuoteSession.fund_asset_allocation``
``get_industry_distribution`` :meth:`FundMobSource.fetch_industry_distribution` / ``WebQuoteSession.fund_industry_distribution``
``get_public_dates``  :meth:`FundMobSource.fetch_public_dates` / ``WebQuoteSession.fund_public_dates``
``get_quote_history`` （已有，见 :mod:`tstdx.web.adapters_fund` ``fund_nav_history``）
``get_realtime_increase_rate`` （已有，见 ``adapters_fund.fund_estimate``）
``get_fund_codes``    （已有，见 ``adapters_fund.fund_list``）
====================  ==========================================

接口事实（efinance 源码逆向，``fundmobapi.eastmoney.com/FundMNewApi/*`` 返回 JSON）：

* 基础信息 ``FundMNNBasicInformation`` → ``Datas``（dict）。
* 持仓     ``FundMNInverstPosition`` → ``Datas.fundStocks``（list）+ ``Expansion``（日期）。
* 阶段涨幅 ``FundMNPeriodIncrease``  → ``Datas``（list）。
* 资产配置 ``FundMNAssetAllocationNew`` → ``Datas``（list，按日期）。
* 行业分布 ``FundMNSectorAllocation`` → ``Datas``（list，按日期）。
* 公开日期 ``FundMNIVInfoMultiple``  → ``Datas``（日期字符串 list）。
* 基金经理 ``fundf10.eastmoney.com/jjjl_{code}.html``（HTML，best-effort 解析）。

.. note::
   移动端接口偶有鉴权变动；任何解析失败统一抛
   :class:`~tstdx.errors.SourceDeprecated`，便于上层降级。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from ..errors import SourceDeprecated
from .base import BaseWebSource, num_f, num_i
from .sources import FUND

__all__ = ["FundMobSource"]

_BASE = "https://fundmobapi.eastmoney.com/FundMNewApi"
_F10 = "https://fundf10.eastmoney.com"
#: efinance 使用的固定设备指纹（移动端接口需要，非隐私字段）。
_DEVICE = "3EA024C2-7F22-408B-95E4-383D38160FB3"
_COMMON = (
    f"&deviceid={_DEVICE}&plat=Iphone&product=EFund&appType=ttjj&serverVersion=6.3.8&version=6.3.8"
)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class FundMobSource(BaseWebSource):
    """东财基金移动端数据源（对标 efinance.fund 扩展能力）。

    Quick start::

        from tstdx.web.efinance_fund import FundMobSource
        src = FundMobSource()
        info    = src.fetch_base_info("161725")          # -> dict
        mgr     = src.fetch_manager("161725")            # -> dict | None
        hold    = src.fetch_holdings("161725")           # -> list[dict]
        period  = src.fetch_period_change("161725")      # -> list[dict]
        alloc   = src.fetch_asset_allocation("161725")    # -> list[dict]
        indu    = src.fetch_industry_distribution("161725")  # -> list[dict]
        dates   = src.fetch_public_dates("161725")       # -> list[str]
        src.close()
    """

    BASE = _BASE
    encoding = "utf-8"

    def __init__(self, **kwargs: Any) -> None:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_3 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        )
        headers.setdefault("Referer", "http://fund.eastmoney.com/")
        super().__init__(headers=headers, **kwargs)

    @property
    def source_name(self) -> str:
        return FUND

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("FundMobSource 为数据型源，请使用 fetch_* 方法")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    # -- 内部取数 ----------------------------------------------------------- #
    def _get_json(self, url: str) -> dict[str, Any]:
        text = self._request_text(url, encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "基金移动端接口返回非 JSON",
                context={"source": FUND, "sample": text[:200]},
                cause=exc,
            ) from exc
        if not isinstance(payload, dict):
            raise SourceDeprecated(
                "基金移动端接口响应非 JSON 对象",
                context={"source": FUND, "sample": text[:200]},
            )
        return payload

    # -- 基础信息 ----------------------------------------------------------- #
    def fetch_base_info(self, code: str) -> dict[str, Any]:
        """基金基础信息（成立日期 / 最新净值 / 基金公司 / 简介）。

        Returns
        -------
        ``{"code","name","establish_date","change_pct","unit_nav","fund_company",
        "nav_date","comment"}``
        """
        url = f"{_BASE}/FundMNNBasicInformation?FCODE={code}{_COMMON}"
        payload = self._get_json(url)
        d = payload.get("Datas") or {}
        if not d:
            return {
                "code": code,
                "name": "",
                "establish_date": "",
                "change_pct": None,
                "unit_nav": None,
                "fund_company": "",
                "nav_date": "",
                "comment": "",
            }
        return {
            "code": _s(d.get("FCODE")),
            "name": _s(d.get("SHORTNAME")),
            "establish_date": _s(d.get("ESTABDATE")),
            "change_pct": num_f(d.get("RZDF")),
            "unit_nav": num_f(d.get("DWJZ")),
            "fund_company": _s(d.get("JJGS")),
            "nav_date": _s(d.get("FSRQ")),
            "comment": _s(d.get("COMMENTS")),
        }

    # -- 基金经理（HTML best-effort） --------------------------------------- #
    def fetch_manager(self, code: str) -> dict[str, Any] | None:
        """基金经理信息（``fundf10`` HTML 解析，best-effort）。

        返回 ``{"fund_code","manager","company","appoint_date","fund_type",
        "scale","current_date"}``；解析失败（页面改版）返回 ``None`` 而非抛错。

        .. warning::
           HTML 结构易变，本解析为容错实现：命中不到经理名/任职日期时对应字段
           留空。接口改版后请重新抓包校准。
        """
        url = f"{_F10}/jjjl_{code}.html"
        text = self._request_text(url, encoding="utf-8")
        # 容错：万一返回非 HTML（如 404 页）直接给空。
        if (
            "<!doctype html>" not in text[:120].lower()
            and "html" not in text[:200].lower()
            and ("页面未找到" in text[:600] or "404" in text[:200])
        ):
            return None
        # 任职日期（YYYY-MM-DD）
        appoint = ""
        m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if m:
            appoint = m.group(1)
        # 基金经理姓名：jjjl 页面经理名通常出现在 <a> 链接或 label 文本
        names: list[str] = []
        for mm in re.finditer(r"<a[^>]*>([^<]{2,12})</a>", text):
            nm = mm.group(1).strip()
            if nm and nm not in names:
                names.append(nm)
        # 基金公司 / 类型 / 规模：从 label 文本中粗取
        company = ""
        cm = re.search(r"基金公司[：:>\s]*([\u4e00-\u9fa5A-Za-z0-9（）()]+)", text)
        if cm:
            company = cm.group(1).strip(" >")
        return {
            "fund_code": code,
            "manager": "、".join(names[:5]) if names else "",
            "company": company,
            "appoint_date": appoint,
            "fund_type": "",
            "scale": "",
            "current_date": "",
        }

    # -- 持仓 --------------------------------------------------------------- #
    def fetch_holdings(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """基金公开持仓（股票代码 / 简称 / 持仓占比 / 较上期变化）。

        Parameters
        ----------
        dates:
            公开日期（``"2021-03-31"``）或日期 list；``None`` 取最新一期。

        Returns
        -------
        ``[{"date","code","name","ratio","change"}, ...]``
        """
        date_list: list[str] = []
        if isinstance(dates, str):
            date_list = [dates]
        elif dates:
            date_list = list(dates)

        out: list[dict[str, Any]] = []
        date_values: Sequence[str | None] = date_list if date_list else (None,)
        for dt in date_values:
            url = f"{_BASE}/FundMNInverstPosition?FCODE={code}{_COMMON}"
            if dt:
                url += f"&DATE={dt}"
            payload = self._get_json(url)
            datas = payload.get("Datas") or {}
            stocks = datas.get("fundStocks") or []
            date_val = _s(payload.get("Expansion")) or _s(datas.get("FSRQ")) or _s(dt)
            for r in stocks:
                if not isinstance(r, dict):
                    continue
                out.append(
                    {
                        "date": date_val,
                        "code": _s(r.get("GPDM")),
                        "name": _s(r.get("GPJC")),
                        "ratio": num_f(r.get("JZBL")),
                        "change": _s(r.get("PCTNVCHG")),
                    }
                )
        return out

    # -- 阶段涨幅 ----------------------------------------------------------- #
    def fetch_period_change(self, code: str) -> list[dict[str, Any]]:
        """阶段涨幅（近1周 / 近1月 / 近3月 / 近6月 / 近1年 / 近3年等）。

        Returns
        -------
        ``[{"period","return_rate","peer_avg","rank","peer_total","fund_code"}, ...]``
        """
        url = f"{_BASE}/FundMNPeriodIncrease?FCODE={code}{_COMMON}"
        payload = self._get_json(url)
        rows = payload.get("Datas") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "period": _s(r.get("title")),
                    "return_rate": num_f(r.get("syl")),
                    "peer_avg": num_f(r.get("avg")),
                    "rank": num_i(r.get("rank")),
                    "peer_total": num_i(r.get("sc")),
                    "fund_code": code,
                }
            )
        return out

    # -- 资产配置（股票/债券/现金占比） ------------------------------------- #
    def fetch_asset_allocation(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """资产配置（股票 / 债券 / 现金 / 其他占比 + 总规模）。

        Parameters
        ----------
        dates:
            公开日期或日期 list；``None`` 取最新一期。

        Returns
        -------
        ``[{"date","stock","bond","cash","other","total_scale","fund_code"}, ...]``
        比例为百分数（``50.0`` 即 50.00%）；``total_scale`` 单位亿元。
        """
        date_list: list[str] = []
        if isinstance(dates, str):
            date_list = [dates]
        elif dates:
            date_list = list(dates)

        out: list[dict[str, Any]] = []
        date_values: Sequence[str | None] = date_list if date_list else (None,)
        for dt in date_values:
            url = f"{_BASE}/FundMNAssetAllocationNew?FCODE={code}{_COMMON}"
            if dt:
                url += f"&DATE={dt}"
            payload = self._get_json(url)
            rows = payload.get("Datas") or []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                out.append(
                    {
                        "date": _s(r.get("FSRQ")) or _s(dt),
                        "stock": num_f(r.get("GP")),
                        "bond": num_f(r.get("ZQ")),
                        "cash": num_f(r.get("HB")),
                        "other": num_f(r.get("QT")),
                        "total_scale": num_f(r.get("JZC")),
                        "fund_code": code,
                    }
                )
        return out

    # -- 行业分布 ----------------------------------------------------------- #
    def fetch_industry_distribution(
        self, code: str, dates: Sequence[str] | str | None = None
    ) -> list[dict[str, Any]]:
        """行业分布（各行业持仓比例 + 市值）。

        Parameters
        ----------
        dates:
            公开日期或日期 list；``None`` 取最新一期。

        Returns
        -------
        ``[{"date","industry","ratio","market_value","fund_code"}, ...]``
        （按日期去重）。
        """
        date_list: list[str] = []
        if isinstance(dates, str):
            date_list = [dates]
        elif dates:
            date_list = list(dates)

        out: list[dict[str, Any]] = []
        date_values: Sequence[str | None] = date_list if date_list else (None,)
        for dt in date_values:
            url = f"{_BASE}/FundMNSectorAllocation?FCODE={code}{_COMMON}"
            if dt:
                url += f"&DATE={dt}"
            payload = self._get_json(url)
            rows = payload.get("Datas") or []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                out.append(
                    {
                        "date": _s(r.get("FSRQ")) or _s(dt),
                        "industry": _s(r.get("HYMC")),
                        "ratio": num_f(r.get("ZJZBL")),
                        "market_value": num_f(r.get("SZ")),
                        "fund_code": code,
                    }
                )
        # 按 (date, industry) 去重
        seen: set[tuple[str, str]] = set()
        dedup: list[dict[str, Any]] = []
        for r in out:
            k = (r.get("date", ""), r.get("industry", ""))
            if k not in seen:
                seen.add(k)
                dedup.append(r)
        return dedup

    # -- 公开日期 ----------------------------------------------------------- #
    def fetch_public_dates(self, code: str) -> list[str]:
        """基金历史上公开更新持仓 / 资产配置的日期列表（旧→新）。"""
        url = f"{_BASE}/FundMNIVInfoMultiple?FCODE={code}{_COMMON}"
        payload = self._get_json(url)
        rows = payload.get("Datas") or []
        return [str(r) for r in rows if r]
