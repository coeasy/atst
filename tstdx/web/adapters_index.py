# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""东财指数成分股数据源适配器（P0-2，§OPTIMIZATION_PLAN_v3）。

接口事实（2026-09 实测验证，tstdx 自有实现）：
* 东财数据中心报表 ``RPT_INDEX_TS_COMPONENT``（指数成分）::

      https://datacenter-web.eastmoney.com/api/data/v1/get
        ?reportName=RPT_INDEX_TS_COMPONENT&columns=ALL
        &filter=(TYPE="1")&pageSize=500&pageNumber=1
        &source=WEB&client=WEB

  - ``TYPE`` 为指数族过滤器（1=沪深300 / 2=上证50 / 3=中证500 /
    4=科创50 / 5=中证A50 / 6=中证A500 / 7=中证1000 / 8=深证50 /
    9=深证100 / 10=北证50 / 11=上证180 / 12=中证A100 / 13=中证2000）；
    其中 5 族（沪深300 / 中证500 / 中证A50 / 中证A500 / 中证A100）已与
    中证官网成分 XLS 交叉验证 jaccard=1.0。
  - 响应 ``result.data`` 为成分行，含 ``SECURITY_CODE`` /
    ``SECURITY_NAME_ABBR`` / ``WEIGHT``(权重%，仅部分指数族) /
    ``INDUSTRY`` / ``PE`` / ``EPS`` / ``CLOSE_PRICE`` / ``CHANGE_RATE`` 等；
    ``result.count`` 为成分总数。
  - **单页上限 500**，中证1000（1000 只）/ 中证2000（2000 只）等大指数
    必须分页拉全量。
  - 无需 Referer（与 datacenter 报表族一致）。

设计说明
--------
* 数据型源：``fetch_constituents`` 直接返回 ``list[dict]``，不经行情模型。
* 复用 :class:`~tstdx.web.corporate.EastmoneyDataCenterSource` 的
  datacenter 主机池 / 报表 URL 构造 / 限流 / 失败计数与下线检测。
* 不参与行情降级链；注册仅用于 discover 与进程级限流桶。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import WebSourceError
from .base import num_f as _f
from .corporate import EastmoneyDataCenterSource
from .sources import INDEX_CONS


def _fopt(value):
    """None 保留变体：指数成分可选字段（weight/pe 等）缺失时保持 None，不填充 0。"""
    return None if value is None else _f(value)


__all__ = ["EastmoneyIndexConstituentsSource", "INDEX_TYPE_MAP"]

#: 指数代码 → 东财 ``RPT_INDEX_TS_COMPONENT`` 的 ``TYPE`` 值
#: （2026-09 实测计数 + 中证官网 XLS 交叉验证）。
INDEX_TYPE_MAP: dict[str, str] = {
    "000300": "1",  # 沪深300（300 只）
    "000016": "2",  # 上证50（50 只）
    "000905": "3",  # 中证500（500 只）
    "000688": "4",  # 科创50（50 只）
    "930050": "5",  # 中证A50（50 只）
    "000510": "6",  # 中证A500（500 只）
    "000852": "7",  # 中证1000（1000 只）
    "399850": "8",  # 深证50（50 只）
    "399330": "9",  # 深证100（100 只）
    "899050": "10",  # 北证50（50 只）
    "000010": "11",  # 上证180（180 只）
    "000903": "12",  # 中证A100（100 只）
    "932000": "13",  # 中证2000（2000 只）
}

#: 单页上限（datacenter-web 报表页容量）
_PAGE_SIZE = 500
#: 翻页安全上限（防御接口异常导致死循环）
_MAX_PAGES = 20


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _resolve_index_code(index: str) -> str:
    """``sh000300`` / ``000300.SH`` / ``000300`` → ``000300``。"""
    s = str(index).strip().lower()
    for market in ("sh", "sz", "bj"):
        if s.startswith(market):
            s = s[len(market) :]
            break
    return s.split(".")[0]


class EastmoneyIndexConstituentsSource(EastmoneyDataCenterSource):
    """东财指数成分股数据源。

    能力：``index_constituents``（指数成分股列表）。

    Quick start::

        from tstdx.web.adapters_index import EastmoneyIndexConstituentsSource
        src = EastmoneyIndexConstituentsSource()
        rows = src.fetch_constituents("000300")   # -> list[dict]（沪深300 约 300 只）
        src.close()
    """

    JSON_LABEL = "指数成分"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("RPT_INDEX_TS_COMPONENT", **kwargs)

    @property
    def source_name(self) -> str:
        return INDEX_CONS

    def fetch_constituents(self, index: str) -> list[dict[str, Any]]:
        """指数成分股列表（分页拉全量）。

        Parameters
        ----------
        index:
            指数代码（``000300`` / ``000905`` / ``930050`` 等），可带市场前缀
            （``sh000300`` / ``sz399330`` / ``bj899050``）。

        Returns
        -------
        ``list[dict]``，每条含 code / name / secucode / weight / industry /
        region / price / change_pct / pe / eps / roe / bps / total_shares /
        free_shares / free_cap / type；``weight`` 仅部分指数族提供
        （沪深300 / 上证50 / 中证500 / 科创50 有值，其余可能为 ``None``）。
        """
        code = _resolve_index_code(index)
        try:
            type_ = INDEX_TYPE_MAP[code]
        except KeyError:
            raise WebSourceError(
                f"未知指数代码: {code!r}；支持: {sorted(INDEX_TYPE_MAP)}",
                context={"source": INDEX_CONS, "index": index},
            ) from None

        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.fetch_rows(
                filters=[f'TYPE="{type_}"'],
                sort_columns="",
                sort_types="-1",
                page=page,
                size=_PAGE_SIZE,
            )
            rows.extend(batch)
            if not batch or len(batch) < _PAGE_SIZE or page >= _MAX_PAGES:
                break
            page += 1
        return [self._normalize(r) for r in rows]

    @staticmethod
    def _normalize(r: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "code": _s(r.get("SECURITY_CODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR")),
            "secucode": _s(r.get("SECUCODE")),
            "weight": _fopt(r.get("WEIGHT")),
            "industry": _s(r.get("INDUSTRY")),
            "region": _s(r.get("REGION")),
            "price": _fopt(r.get("CLOSE_PRICE")),
            "change_pct": _fopt(r.get("CHANGE_RATE")),
            "pe": _fopt(r.get("PE")),
            "eps": _fopt(r.get("EPS")),
            "roe": _fopt(r.get("ROE")),
            "bps": _fopt(r.get("BPS")),
            "total_shares": _fopt(r.get("TOTAL_SHARES")),
            "free_shares": _fopt(r.get("FREE_SHARES")),
            "free_cap": _fopt(r.get("FREE_CAP")),
            "type": _s(r.get("TYPE")),
        }
