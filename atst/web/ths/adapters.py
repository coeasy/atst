# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""同花顺（10jqka）适配器：涨停池 / 板块归属 / 概念成分 / 人气榜。

端点（2026-10-02 真机验证）
--------------------------
* 涨停池
  ``https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool``
* 涨停板块归属（板块 → 成分股）
  ``https://data.10jqka.com.cn/dataapi/limit_up/block_top``
* 人气榜
  ``https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock``

三个端点均需 ``Referer: https://data.10jqka.com.cn/``。

未收录：``northbound``（北向资金）与 ``earnings_forecast``（盈利预测）——
对应端点未能取到可用响应，按「不留死能力」纪律不登记。
"""

from __future__ import annotations

import json
from typing import Any

from ..base import BaseWebSource
from ..sources import THS

__all__ = ["ThsSource"]

_DATA = "https://data.10jqka.com.cn/dataapi/limit_up"
_DQ = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"

#: market_id → 交易所前缀（同花顺用 17=沪、33=深）
_MARKET_PREFIX = {17: "sh", 33: "sz"}


class ThsSource(BaseWebSource):
    """同花顺行情补充源（涨停池 / 板块 / 人气）。"""

    encoding = "utf-8"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"Referer": "https://data.10jqka.com.cn/"})
        super().__init__(**kwargs)

    @property
    def source_name(self) -> str:
        return THS

    def build_url(self, symbols: Any, **kwargs: Any) -> str:
        return f"{_DATA}/limit_up_pool"

    def parse(self, text: str, symbols: Any, **kwargs: Any) -> list[Any]:
        return self._parse_pool(json.loads(text))

    # -- 能力出口（executor 按 binding.method 分派） ------------------------ #

    def fetch_limit_pool(
        self, symbol: str | None = None, *, date: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """涨停池：当日/指定日期涨停个股。

        Parameters
        ----------
        symbol:
            可选。给了就只留这只股（用于「它今天涨停了吗」这类判定）；不给返回全市场。
            上游没有按标的过滤的参数，筛选在**本地**完成——取回的是完整涨停池，
            再按代码比对，不是服务端过滤。
        date:
            ``YYYYMMDD``；为空取最新交易日。
        limit:
            返回条数上限。
        """
        url = f"{_DATA}/limit_up_pool?page=1&limit={int(limit)}&filter=HS,GEM2STAR"
        if date:
            url += f"&date={date}"
        else:
            url += "&order_field=330324&order_type=0"
        rows = self._parse_pool(json.loads(self._get(url)))
        if symbol:
            code = _code_of(symbol)
            return [r for r in rows if r["code"] == code][:limit]
        return rows[:limit]

    def fetch_theme_attribution(
        self, symbol: str | None = None, *, date: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """涨停板块归属：板块维度聚合（含该板块涨停家数、最高连板）。

        ``symbol`` 为板块代码时只返回该板块；为空返回全部板块。
        """
        #: 上游 ``block_top`` 忽略 ``limit`` 查询参数（实测 limit=3 仍返回全部板块），
        #: 因此只能在本地截断——否则调用方拿到的条数与请求的不一致。
        blocks = self._parse_blocks(json.loads(self._get(_block_top_url(limit, date))))
        if symbol:
            code = _code_of(symbol)
            return [b for b in blocks if b["code"] == code]
        return blocks[:limit]

    def fetch_concept_members(
        self, symbol: str | None = None, *, date: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """概念成分股：把板块归属里的 ``stock_list`` 摊平成个股清单。

        ``symbol`` 语义按「它落在哪一类」分流，不能再写两道互相抵消的过滤：

        * 板块代码（``885431`` 这类）→ 只留该板块的成分；
        * 个股代码（``002454`` 这类）→ 只留这只股（它可能出现在多个板块）；
        * 空 → 返回全部成分（本地按 ``limit`` 截断，上游 ``block_top`` 忽略该参数）。
        """
        blocks = self._parse_blocks(json.loads(self._get(_block_top_url(limit, date))))
        out: list[dict[str, Any]] = []
        for b in blocks:
            for st in b.get("members", []):
                out.append({**st, "block_code": b["code"], "block_name": b["name"]})
        if symbol:
            code = _code_of(symbol)
            block_codes = {b["code"] for b in blocks}
            if code in block_codes:
                #: 板块代码 → 只留该板块的成分（两道过滤互斥，只能留一道）。
                return [r for r in out if r["block_code"] == code]
            #: 个股代码 → 只留这只股（可能跨多个板块出现）。
            return [r for r in out if r["code"] == code]
        #: 同 ``fetch_theme_attribution``：上游忽略 ``limit``，本地截断。
        return out[:limit]

    def fetch_hot_rank(self, symbol: str | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
        """人气榜（小时级热度排行）。

        Parameters
        ----------
        symbol:
            可选。给了就只留这只股（看它的人气排名与热度值）；不给返回完整榜单。
            同 ``fetch_limit_pool``：上游无按标的过滤参数，筛选在本地完成。
        """
        url = f"{_DQ}?stock_type=a&type=hour&list_type=normal"
        return self._parse_hot_rank(json.loads(self._get(url)), symbol=symbol)[:limit]

    @staticmethod
    def _parse_hot_rank(
        payload: dict[str, Any], *, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        rows = (payload.get("data") or {}).get("stock_list") or []
        out: list[dict[str, Any]] = []
        for i, item in enumerate(rows, start=1):
            code = str(item.get("code") or "")
            market_id = item.get("market")
            out.append(
                {
                    "rank": int(item.get("order") or i),
                    "symbol": f"{_MARKET_PREFIX.get(market_id, '')}{code}",
                    "code": code,
                    "name": item.get("name") or "",
                    "hot_value": _to_float(item.get("rate")),
                    "change_rate": _to_float(item.get("rise_and_fall")),
                    "rank_change": int(item.get("hot_rank_chg") or 0),
                    "concepts": list((item.get("tag") or {}).get("concept_tag") or []),
                    "source": "ths",
                }
            )
        if symbol:
            code = _code_of(symbol)
            out = [r for r in out if r["code"] == code]
        return out

    # -- 内部 -------------------------------------------------------------- #

    def _get(self, url: str) -> str:
        self._check_deprecated()
        self.rate_limiter.acquire(self.source_name)
        return self._request_text(url, encoding="utf-8", err_msg="同花顺请求失败")

    @staticmethod
    def _parse_pool(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = ((payload.get("data") or {}).get("info")) or []
        out: list[dict[str, Any]] = []
        for item in rows:
            code = str(item.get("code") or "")
            market_id = item.get("market_id")
            out.append(
                {
                    "symbol": f"{_MARKET_PREFIX.get(market_id, '')}{code}",
                    "code": code,
                    "name": item.get("name") or "",
                    "market_id": market_id,
                    "change_tag": item.get("change_tag") or "",
                    "is_new": int(item.get("is_new") or 0),
                    "is_again_limit": int(item.get("is_again_limit") or 0),
                    "high_days": item.get("high_days_value"),
                    "source": "ths",
                }
            )
        return out

    @staticmethod
    def _parse_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("data") or []
        out: list[dict[str, Any]] = []
        for item in rows:
            out.append(
                {
                    "code": str(item.get("code") or ""),
                    "name": item.get("name") or "",
                    "change": _to_float(item.get("change")),
                    "limit_up_num": int(item.get("limit_up_num") or 0),
                    "continuous_plate_num": int(item.get("continuous_plate_num") or 0),
                    "high": item.get("high") or "",
                    "days": int(item.get("days") or 0),
                    "members": [
                        {
                            "symbol": f"{_MARKET_PREFIX.get(m.get('market_id'), '')}{m.get('code', '')}",
                            "code": str(m.get("code") or ""),
                            "change_rate": _to_float(m.get("change_rate")),
                            "reason_type": m.get("reason_type") or "",
                            "high": m.get("high") or "",
                        }
                        for m in (item.get("stock_list") or [])
                    ],
                    "source": "ths",
                }
            )
        return out


def _block_top_url(limit: int, date: str | None) -> str:
    url = f"{_DATA}/block_top?limit={int(limit)}"
    if date:
        url += f"&date={date}"
    return url


def _code_of(symbol: str) -> str:
    s = str(symbol).strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            return s[len(prefix) :]
    return s


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
