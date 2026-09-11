# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""新浪 ESG 评级数据源（MSCI / 标普 / 华证 / 商道融绿 / 智多 / 路孚特 / 秩鼎等 13 家机构）。

补齐 tstdx 此前缺失的 **ESG 环境社会治理** 数据域——提示词模板维度 9 的关键缺口。
tstdx 无 ESG 数据时，ESG 表现与治理分析只能依赖定性判断，本模块将其量化。

接口事实（2026-09-11 抓包验证，tstdx 自有实现）::

    # 个股 ESG 评级详情（13 家机构 × 评级 + 评分 + E/S/G 分项）
    https://global.finance.sina.com.cn/api/openapi.php/EsgService.getEsgStockInfo
      ?symbol=sh600519

    // 成功:
    {"result":{"status":{"code":0},"data":{"code":200000,"info":[
      {"agency":"agency13","agency_name":"MSCI","esg_score":"A",
       "esg_level":"A","esg_dt":"2026-06-22",
       "detail":[{"name":"环境总评","date":"2026-06-22","score":"7.48"},
                 {"name":"社会责任总评","date":"2026-06-22","score":"6.01"},
                 {"name":"治理总评","date":"2026-06-22","score":"4.4"}]}]}}}

    # 个股 ESG 评级历史（季度变动）
    https://quotes.sina.cn/cn/api/openapi.php/EsgService.getEsgStockHistory
      ?symbol=sh600519

    // 成功:
    {"result":{"status":{"code":0},"data":[
      {"agency":"agency6","agency_name":"伦交所",
       "history":{"2025Q2":"61.9","2025Q3":"61.4","2026Q1":"57.6"},
       "type":"num"},
      {"agency":"agency12","agency_name":"华证指数",
       "history":{"2025Q3":"AAA","2026Q2":"AAA"},"type":"string"}]}}

    # MSCI 全市场 ESG 评级列表（5200+ 只，含港股）
    https://global.finance.sina.com.cn/api/openapi.php/EsgService.getMsciEsgStocks

    // 成功:
    {"result":{"status":{"code":0},"data":{"total":"5216","data":[
      {"symbol":"000001.SZ","quarter_date":"2026-07-08","market":"CN",
       "esg_rating":"AAA","env_score":"6.3","social_score":"6.0",
       "governance_score":"5.4"}]}}}

    # 华证全市场 ESG 评级列表（6300+ 只）
    https://global.finance.sina.com.cn/api/openapi.php/EsgService.getHzEsgStocks

    // 成功:
    {"result":{"status":{"code":0},"data":{"total":"6355","data":[
      {"date":"2026-04-30","symbol":"603605.SH","market":"cn",
       "name":"珠利雅","esg_score":"100","esg_score_grade":"AAA",
       "e_score":"89.46","e_score_grade":"A","s_score":"88.5",
       "s_score_grade":"A","g_score":"92.17","g_score_grade":"AA"}]}}}

.. note::
   新浪 ESG 接口为 `openapi.php` 风格（非 datacenter-web 报表族），
   响应结构为 ``{result:{status:{code:0},data:{...}}}``——
   与东财 datacenter-web 的 ``{result:{data:[...]}}`` 不同，
   故本模块独立实现 JSON 解析，不复用 :class:`EastmoneyDataCenterSource`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from ..errors import ReadTimeout, SourceDeprecated, WebSourceError
from .base import BaseWebSource
from .sources import SINA

__all__ = [
    "SinaEsgStockInfoSource",
    "SinaEsgHistorySource",
    "SinaEsgMsciSource",
    "SinaEsgHzSource",
]

# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _num(value: Any) -> float | None:
    """空值/NaN 转 None，其余转 float。"""
    if value is None or value == "":
        return None
    try:
        v = float(value)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def _s(value: Any) -> str:
    return "" if value is None else str(value)


# --------------------------------------------------------------------------- #
# 新浪 JSON 公共基类（单主机 GET + JSON 解析）
# --------------------------------------------------------------------------- #
class _SinaJson(BaseWebSource):
    """新浪 JSON 接口公共基类：单主机 GET + ``resp.json()``。

    与 :class:`_EastmoneyJson` 的区别：新浪为单主机（无主机池 failover），
    响应结构为 ``{result:{status,data}}``（非东财的 ``{data:{list}}``）。
    本类提供 :meth:`_get_json` 供各 ESG Source 复用。
    """

    HOSTS: tuple[str, ...] = ()
    JSON_LABEL = "新浪ESG"
    encoding = "utf-8"

    def _get_json(self, path_query: str, *, host: str | None = None) -> dict[str, Any]:
        """单主机 GET + JSON 解析（计入失败桶 / 下线检测）。"""
        self._check_deprecated()
        hosts: Sequence[str] = self.HOSTS or (self.BASE,)
        target = host or hosts[0]
        url = f"{target}{path_query}"
        try:
            resp = self.client.get(url, headers=self.headers, timeout=self.timeout)
        except (WebSourceError, ReadTimeout) as exc:
            self._record_failure()
            raise WebSourceError(
                f"{self.JSON_LABEL}传输失败: {exc}",
                context={"source": self.source_name, "host": target},
                cause=exc,
            ) from exc
        if not resp.ok:
            self._record_failure()
            raise WebSourceError(
                f"{self.JSON_LABEL}请求失败 HTTP {resp.status}",
                context={"source": self.source_name, "host": target},
            )
        try:
            payload = resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._record_failure(parse=True)
            raise SourceDeprecated(
                f"{self.JSON_LABEL}响应非 JSON",
                context={
                    "source": self.source_name,
                    "host": target,
                    "sample": resp.text(self.encoding)[:120],
                },
                cause=exc,
            ) from exc
        self._reset_failures()
        return payload


# --------------------------------------------------------------------------- #
# 个股 ESG 评级详情
# --------------------------------------------------------------------------- #
class SinaEsgStockInfoSource(_SinaJson):
    """个股 ESG 评级详情（新浪，13 家机构聚合）。

    返回每家机构的 ESG 评级（如 AAA / A / BBB）、评级日期、
    以及环境/社会/治理三个维度的分项评分。
    """

    BASE = "https://global.finance.sina.com.cn"
    HOSTS = (BASE,)
    JSON_LABEL = "新浪ESG评级"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = self._to_sina_symbol(symbols[0] if symbols else "")
        return (
            f"/api/openapi.php/EsgService.getEsgStockInfo"
            f"?symbol={quote(symbol)}"
        )

    @staticmethod
    def _to_sina_symbol(symbol: str) -> str:
        """转新浪格式：``sh600519``。"""
        from ..domain.symbol import split_symbol

        market, code = split_symbol(symbol)
        prefix = {"sh": "sh", "sz": "sz", "bj": "bj"}.get(market, "sh")
        return f"{prefix}{code}"

    def fetch_stock_info(self, symbol: str) -> dict[str, Any] | None:
        """获取个股 ESG 评级详情。

        Returns
        -------
        ``{"symbol","agencies":[{"agency_name","esg_score","esg_level",
        "esg_dt","e_score","s_score","g_score"}, ...]} | None``
        """
        self.rate_limiter.acquire(self.source_name)
        url = self.build_url([symbol])
        payload = self._get_json(url)
        return self._parse_stock_info(payload)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _parse_stock_info(self, payload: Any) -> dict[str, Any] | None:
        """解析 ``{result:{status,data}}`` 结构。"""
        result = payload.get("result") or {}
        data = result.get("data") or {}
        if not data:
            return None
        info = data.get("info") or []
        agencies: list[dict[str, Any]] = []
        for item in info:
            if not isinstance(item, Mapping):
                continue
            # 提取 E/S/G 分项
            detail = item.get("detail") or []
            e_score = s_score = g_score = None
            for d in detail:
                if not isinstance(d, Mapping):
                    continue
                name = _s(d.get("name", ""))
                score = _num(d.get("score"))
                if "环境" in name or "环保" in name:
                    e_score = score
                elif "社会" in name or "责任" in name:
                    s_score = score
                elif "治理" in name:
                    g_score = score
            agencies.append({
                "agency_name": _s(item.get("agency_name")),
                "esg_score": _s(item.get("esg_score") or item.get("esg_level")),
                "esg_level": _s(item.get("esg_level")),
                "esg_dt": _s(item.get("esg_dt")),
                "e_score": e_score,
                "s_score": s_score,
                "g_score": g_score,
            })
        return {"agencies": agencies}


# --------------------------------------------------------------------------- #
# 个股 ESG 评级历史
# --------------------------------------------------------------------------- #
class SinaEsgHistorySource(_SinaJson):
    """个股 ESG 评级历史（新浪，季度变动）。"""

    BASE = "https://quotes.sina.cn"
    HOSTS = (BASE,)
    JSON_LABEL = "新浪ESG历史"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = self._to_sina_symbol(symbols[0] if symbols else "")
        return (
            f"/cn/api/openapi.php/EsgService.getEsgStockHistory"
            f"?symbol={quote(symbol)}"
        )

    @staticmethod
    def _to_sina_symbol(symbol: str) -> str:
        from ..domain.symbol import split_symbol

        market, code = split_symbol(symbol)
        prefix = {"sh": "sh", "sz": "sz", "bj": "bj"}.get(market, "sh")
        return f"{prefix}{code}"

    def fetch_history(self, symbol: str) -> dict[str, Any] | None:
        """获取个股 ESG 评级历史（按机构分组）。

        Returns
        -------
        ``{"agencies":[{"agency_name","type","history":
        {"2025Q2":"61.9","2026Q1":"57.6",...}}]} | None``
        """
        self.rate_limiter.acquire(self.source_name)
        url = self.build_url([symbol])
        payload = self._get_json(url)
        return self._parse_history(payload)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _parse_history(self, payload: Any) -> dict[str, Any] | None:
        result = payload.get("result") or {}
        data = result.get("data") or []
        if not data:
            return None
        agencies: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, Mapping):
                continue
            agencies.append({
                "agency_name": _s(item.get("agency_name")),
                "type": _s(item.get("type")),
                "history": dict(item.get("history") or {}),
            })
        return {"agencies": agencies}


# --------------------------------------------------------------------------- #
# MSCI 全市场 ESG 评级
# --------------------------------------------------------------------------- #
class SinaEsgMsciSource(_SinaJson):
    """MSCI 全市场 ESG 评级列表（5200+ 只，含 A 股 / 港股）。"""

    BASE = "https://global.finance.sina.com.cn"
    HOSTS = (BASE,)
    JSON_LABEL = "新浪MSCI ESG"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return "/api/openapi.php/EsgService.getMsciEsgStocks"

    def fetch_ratings(
        self,
        *,
        market: str = "",
        rating: str = "",
        sort_column: str = "esg_rating",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """获取 MSCI 全市场 ESG 评级列表。

        Parameters
        ----------
        market:
            市场过滤：``"CN"``（A股）/ ``"HK"``（港股）/ 空串=全市场。
        rating:
            评级过滤：``"AAA"`` / ``"AA"`` / ``"A"`` / ``"BBB"`` / 空串=全部。
        sort_column:
            排序键：``"esg_rating"`` / ``"env_score"`` / ``"social_score"`` /
            ``"governance_score"`` / ``"quarter_date"``。
        sort_order:
            ``"asc"`` / ``"desc"``。

        Returns
        -------
        ``{"total": 5216, "ratings": [{"symbol","quarter_date","market",
        "esg_rating","env_score","social_score","governance_score"}, ...]}``
        """
        self.rate_limiter.acquire(self.source_name)
        url = self.build_url([])
        payload = self._get_json(url)
        return self._parse_ratings(payload, market=market, rating=rating,
                                   sort_column=sort_column, sort_order=sort_order)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _parse_ratings(
        self, payload: Any, *, market: str = "", rating: str = "",
        sort_column: str = "esg_rating", sort_order: str = "desc",
    ) -> dict[str, Any]:
        result = payload.get("result") or {}
        data = result.get("data") or {}
        total = int(_s(data.get("total", 0)) or 0)
        raw = data.get("data") or []
        ratings: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            row = {
                "symbol": _s(item.get("symbol")),
                "quarter_date": _s(item.get("quarter_date")),
                "market": _s(item.get("market")),
                "esg_rating": _s(item.get("esg_rating")),
                "env_score": _num(item.get("env_score")),
                "social_score": _num(item.get("social_score")),
                "governance_score": _num(item.get("governance_score")),
            }
            # 过滤
            if market and row["market"] != market:
                continue
            if rating and row["esg_rating"] != rating:
                continue
            ratings.append(row)
        # 排序
        reverse = sort_order == "desc"
        def _sort_key(r: dict[str, Any]) -> Any:
            val = r.get(sort_column)
            if val is None:
                return (1, "") if isinstance(r.get(sort_column), str) else (1, 0)
            return (0, val)
        ratings.sort(key=_sort_key, reverse=reverse)
        return {"total": total, "ratings": ratings}


# --------------------------------------------------------------------------- #
# 华证全市场 ESG 评级
# --------------------------------------------------------------------------- #
class SinaEsgHzSource(_SinaJson):
    """华证全市场 ESG 评级列表（6300+ 只，含 A 股）。"""

    BASE = "https://global.finance.sina.com.cn"
    HOSTS = (BASE,)
    JSON_LABEL = "新浪华证ESG"

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return "/api/openapi.php/EsgService.getHzEsgStocks"

    def fetch_ratings(
        self,
        *,
        market: str = "",
        grade: str = "",
        sort_column: str = "esg_score",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """获取华证全市场 ESG 评级列表。

        Parameters
        ----------
        market:
            市场过滤：``"cn"`` / 空串=全部。
        grade:
            评级过滤：``"AAA"`` / ``"AA"`` / ``"A"`` / 空串=全部。
        sort_column:
            排序键：``"esg_score"`` / ``"e_score"`` / ``"s_score"`` /
            ``"g_score"`` / ``"date"``。
        sort_order:
            ``"asc"`` / ``"desc"``。

        Returns
        -------
        ``{"total": 6355, "ratings": [{"symbol","name","date","market",
        "esg_score","esg_score_grade","e_score","e_score_grade",
        "s_score","s_score_grade","g_score","g_score_grade"}, ...]}``
        """
        self.rate_limiter.acquire(self.source_name)
        url = self.build_url([])
        payload = self._get_json(url)
        return self._parse_ratings(payload, market=market, grade=grade,
                                   sort_column=sort_column, sort_order=sort_order)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _parse_ratings(
        self, payload: Any, *, market: str = "", grade: str = "",
        sort_column: str = "esg_score", sort_order: str = "desc",
    ) -> dict[str, Any]:
        result = payload.get("result") or {}
        data = result.get("data") or {}
        total = int(_s(data.get("total", 0)) or 0)
        raw = data.get("data") or []
        ratings: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            row = {
                "symbol": _s(item.get("symbol")),
                "name": _s(item.get("name")),
                "date": _s(item.get("date")),
                "market": _s(item.get("market")),
                "esg_score": _num(item.get("esg_score")),
                "esg_score_grade": _s(item.get("esg_score_grade")),
                "e_score": _num(item.get("e_score")),
                "e_score_grade": _s(item.get("e_score_grade")),
                "s_score": _num(item.get("s_score")),
                "s_score_grade": _s(item.get("s_score_grade")),
                "g_score": _num(item.get("g_score")),
                "g_score_grade": _s(item.get("g_score_grade")),
            }
            if market and row["market"] != market:
                continue
            if grade and row["esg_score_grade"] != grade:
                continue
            ratings.append(row)
        reverse = sort_order == "desc"
        def _sort_key(r: dict[str, Any]) -> Any:
            val = r.get(sort_column)
            if val is None:
                return (1, 0)
            return (0, val)
        ratings.sort(key=_sort_key, reverse=reverse)
        return {"total": total, "ratings": ratings}
