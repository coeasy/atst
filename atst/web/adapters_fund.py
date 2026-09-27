# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""东财基金（天天基金）数据源适配器（P0-1，§OPTIMIZATION_PLAN_v3）。

接口事实（公开接口，接入时实测验证）：
* 历史净值: ``api.fund.eastmoney.com/f10/lsjz``
  - ``fundCode={code}&pageIndex={i}&pageSize={n}``
  - 响应 ``Data.LSJZList`` 为 list，**旧→新**排列；每条含
    ``FSRQ``(净值日期) / ``DWJZ``(单位净值) / ``LJJZ``(累计净值) /
    ``JZZZL``(日增长率) / ``JZZZBL``(分红/拆分)。
  - 需 Referer ``http://fundf10.eastmoney.com/``，否则 401。
* 实时估值: ``fundgz.1234567.com.cn/js/{code}.js?rt=<ms>``
  - 返回 jsonp ``jsonpgz({...})``，字段 ``fundcode`` / ``name`` /
    ``jzrq``(净值日期) / ``dwjz``(单位净值) / ``gsz``(估算净值) /
    ``gszzl``(估算涨跌%) / ``gztime``(估算时间)。
* 基金列表: ``fund.eastmoney.com/js/fundcode_search.js``
  - 返回 ``var r = [[代码, 名称, 类型, 拼音全称, 拼音缩写], ...];``
  - 全量约 1.2 万条，一次拉全（数 MB）。

设计说明
--------
* 数据型源：三个 fetch 方法直接返回 ``list[dict]`` / ``dict``，
  不经行情模型（不调用 normalize_quote）；与 corporate/lhb 同类。
* 继承 :class:`~atst.web.base.BaseWebSource` 的限流 / 重试退避 / 失败计数
  + 下线检测（:class:`~atst.errors.SourceDeprecated`）。
* 不参与行情降级链（DEFAULT_FALLBACK_ORDER 不含 fund）；注册仅用于
  discover 与进程级限流桶。
"""

from __future__ import annotations

import json
import time as _time
from collections.abc import Sequence
from typing import Any

from ..errors import SourceDeprecated
from .base import BaseWebSource
from .sources import FUND

__all__ = ["FundSource"]

#: 历史净值接口 Referer（缺失返回 401）
_REFERER = "http://fundf10.eastmoney.com/"


def _parse_jsonp(text: str) -> dict[str, Any]:
    """提取 ``jsonpgz({...})`` 括号内的 JSON 对象。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise SourceDeprecated(
            "基金估值响应非 jsonp",
            context={"source": FUND, "sample": text[:200]},
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SourceDeprecated(
            "基金估值响应 JSON 解析失败",
            context={"source": FUND, "sample": text[:200]},
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise SourceDeprecated(
            "基金估值响应非 JSON 对象",
            context={"source": FUND, "sample": text[:200]},
        )
    return payload


def _parse_fund_list_js(text: str) -> list[list[Any]]:
    """解析 ``var r = [[...], ...];`` 形式的 JS 数组字面量。"""
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise SourceDeprecated(
            "基金列表响应非 JS 数组",
            context={"source": FUND, "sample": text[:200]},
        )
    try:
        arr = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SourceDeprecated(
            "基金列表响应 JSON 解析失败",
            context={"source": FUND, "sample": text[:200]},
            cause=exc,
        ) from exc
    if not isinstance(arr, list):
        raise SourceDeprecated(
            "基金列表响应非数组",
            context={"source": FUND, "sample": text[:200]},
        )
    return arr


def _to_float(value: Any) -> float | None:
    """空串 / None → None（新基金当日无净值），否则转 float。"""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FundSource(BaseWebSource):
    """东财基金数据源（天天基金系）。

    能力：``fund_nav_history``（历史净值）/ ``fund_list``（全量基金列表）。
    ``fetch_estimate``（实时估值）的端点已下线——它**总是**抛
    :class:`~atst.errors.SourceDeprecated`，不是一项可用能力，只保留解析层
    以备端点复活（详见该方法 docstring）。

    Quick start::

        from atst.web.adapters_fund import FundSource
        src = FundSource()
        nav      = src.fetch_nav_history("161725")          # -> list[dict]
        estimate = src.fetch_estimate("161725")             # -> dict
        funds    = src.fetch_fund_list()                    # -> list[dict]
        src.close()
    """

    BASE_NAV = "https://api.fund.eastmoney.com/f10/lsjz"
    BASE_ESTIMATE = "https://fundgz.1234567.com.cn/js"
    BASE_LIST = "https://fund.eastmoney.com/js/fundcode_search.js"
    encoding = "utf-8"

    def __init__(self, **kwargs: Any) -> None:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Referer", _REFERER)
        super().__init__(headers=headers, **kwargs)

    @property
    def source_name(self) -> str:
        return FUND

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        # 数据型源不通过基类 fetch 入口（无统一行情 URL）
        raise NotImplementedError("FundSource 为数据型源，请使用 fetch_* 方法")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    # -- 历史净值 ----------------------------------------------------------- #
    def fetch_nav_history(
        self, code: str, *, page_size: int = 100, page_index: int = 1
    ) -> list[dict[str, Any]]:
        """基金历史净值（旧→新）。

        Parameters
        ----------
        code:
            6 位基金代码（如 ``161725``）。
        page_size:
            单页条数（服务端上限 49 起 ~1000 内有效，默认 100）。
        page_index:
            页码（1 起）。分页拉全量可循环翻页直至返回少于 page_size。

        Returns
        -------
        ``list[dict]``，每条含：date / unit_nav / accum_nav / pct_change /
        bonus_ratio（净值日期、单位净值、累计净值、日增长率、分红/拆分）。
        """
        url = f"{self.BASE_NAV}?fundCode={code}&pageIndex={page_index}&pageSize={page_size}"
        payload = self._get_json(url)
        rows = ((payload.get("Data") or {}).get("LSJZList")) or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "date": r.get("FSRQ"),
                    "unit_nav": _to_float(r.get("DWJZ")),
                    "accum_nav": _to_float(r.get("LJJZ")),
                    "pct_change": _to_float(r.get("JZZZL")),
                    "bonus_ratio": _to_float(r.get("JZZZBL")),
                }
            )
        return out

    # -- 实时估值 ----------------------------------------------------------- #
    def fetch_estimate(self, code: str) -> dict[str, Any]:
        """基金实时估值快照（盘中估算）。

        Returns
        -------
        dict，含：code / name / jzrq（净值日期）/ dwjz（单位净值）/
        gsz（估算净值）/ gszzl（估算涨跌%）/ gztime（估算时间）。
        非交易时段返回最近一次估算；盘后 dwjz 即当日净值。

        Raises
        ------
        SourceDeprecated:
            ``fundgz.1234567.com.cn`` 接口已于 2026 年下线（实测对任意代码
            返回「页面未找到」404 HTML 页；官方 App 内的 FundMNFInfo 接口
            需设备签名鉴权，无公开可用的实时估值替代端点）。保留解析层
            （``_parse_jsonp``）以备接口复活时零改动恢复。
        """
        url = f"{self.BASE_ESTIMATE}/{code}.js?rt={int(_time.time() * 1000)}"
        text = self._request_text(url, encoding="utf-8")
        if "<!doctype html>" in text[:200].lower() or "页面未找到" in text[:600]:
            raise SourceDeprecated(
                "fundgz 实时估值接口已下线（东财返回 404 页面，App 接口需鉴权）；"
                "可用 fund_nav_history 取历史净值替代",
                context={"source": FUND, "code": code, "endpoint": self.BASE_ESTIMATE},
            )
        payload = _parse_jsonp(text)
        return {
            "code": payload.get("fundcode"),
            "name": payload.get("name"),
            "jzrq": payload.get("jzrq"),
            "dwjz": _to_float(payload.get("dwjz")),
            "gsz": _to_float(payload.get("gsz")),
            "gszzl": _to_float(payload.get("gszzl")),
            "gztime": payload.get("gztime"),
        }

    # -- 基金列表 ----------------------------------------------------------- #
    def fetch_fund_list(self) -> list[dict[str, Any]]:
        """全量基金列表（约 1.2 万条，一次拉全，数 MB）。

        Returns
        -------
        ``list[dict]``，每条含：code / name / type / pinyin / py_abbr。
        """
        arr = _parse_fund_list_js(self._request_text(self.BASE_LIST, encoding="utf-8"))
        out: list[dict[str, Any]] = []
        for r in arr:
            if not isinstance(r, list) or not r:
                continue
            out.append(
                {
                    "code": str(r[0]),
                    "name": str(r[1]) if len(r) > 1 else "",
                    "type": str(r[2]) if len(r) > 2 else "",
                    "pinyin": str(r[3]) if len(r) > 3 else "",
                    "py_abbr": str(r[4]) if len(r) > 4 else "",
                }
            )
        return out

    # -- 内部工具 ----------------------------------------------------------- #
    def _get_json(self, url: str) -> dict[str, Any]:
        text = self._request_text(url, encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "基金历史净值返回非 JSON",
                context={"source": FUND, "sample": text[:200]},
                cause=exc,
            ) from exc
        if not isinstance(payload, dict):
            raise SourceDeprecated(
                "基金历史净值响应非 JSON 对象",
                context={"source": FUND, "sample": text[:200]},
            )
        return payload
