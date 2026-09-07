# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""东财融资融券个股明细数据源（P12 扩展，REFACTOR 收尾批次）。

接口事实（2026-09-06 实测，datacenter-web.eastmoney.com）：
* 报表 ``RPTA_WEB_RZRQ_GGMX``（融资融券个股明细，按日）：
  ``/api/data/v1/get?reportName=RPTA_WEB_RZRQ_GGMX&columns=ALL``
  ``&filter=(scode="600519")&sortColumns=DATE&sortTypes=-1``。
* 仅两融标的有个股明细（非标的个股空列表）；``DATE`` 形如
  ``2026-09-04 00:00:00``；金额单位**元**。
* 市场汇总报表 ``RPTA_WEB_RZRQ_LSHJ`` 已不存在（实测「报表配置不存在」），
  仅提供个股维度。

字段对照（报表原始名 → 归一化名）
---------------------------------
===================  =============  ===========================
报表字段              归一化字段      含义（单位）
===================  =============  ===========================
DATE                 date           日期（截断为 YYYY-MM-DD）
SCODE / SECNAME      code / name    代码 / 名称
TRADE_MARKET         market         交易市场（沪证/深证…）
RZYE                 rzye           融资余额（元）
RZMRE                rzmre          融资买入额（元）
RZCHE                rzche          融资偿还额（元）
RZJME                rzjme          融资净买入（元）
RQYE                 rqye           融券余额（元）
RQYL                 rqyl           融券余量（股）
RQMCL                rqmcl          融券卖出量（股）
RZRQYE               rzrqye         融资融券余额（元）
RZRQYECZ             rzrqye_cz      两融余额差值（元）
RZYEZB               rzyezb         融资余额占流通市值比（%）
SZ                   total_mv       总市值（元）
SPJ                  close          收盘价（元）
ZDF                  pct_change     涨跌幅（%）
===================  =============  ===========================

3/5/10 日差分字段（``RZMRE3D/5D/10D`` 等）不逐个映射，保留在每行
``extra``（键名原样）供高级使用。
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import SourceDeprecated
from .corporate import EastmoneyDataCenterSource
from .sources import MARGIN

__all__ = ["EastmoneyMarginSource"]

#: 报表字段 → 归一化字段（金额单位元，量纲照抄不做缩放）
_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("RZYE", "rzye"),
    ("RZMRE", "rzmre"),
    ("RZCHE", "rzche"),
    ("RZJME", "rzjme"),
    ("RQYE", "rqye"),
    ("RQYL", "rqyl"),
    ("RQMCL", "rqmcl"),
    ("RZRQYE", "rzrqye"),
    ("RZRQYECZ", "rzrqye_cz"),
    ("RZYEZB", "rzyezb"),
    ("SZ", "total_mv"),
    ("SPJ", "close"),
    ("ZDF", "pct_change"),
)


def _to_f(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class EastmoneyMarginSource(EastmoneyDataCenterSource):
    """东财融资融券个股明细（数据型源，``list[dict]``）。

    Quick start::

        from tstdx.web.adapters_margin import EastmoneyMarginSource
        src = EastmoneyMarginSource()
        rows = src.fetch_margin("600519", days=10)   # -> list[dict]，DATE 倒序
        src.close()
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("RPTA_WEB_RZRQ_GGMX", **kwargs)

    @property
    def source_name(self) -> str:
        return MARGIN

    def fetch_margin(
        self,
        symbol: str,
        *,
        days: int = 0,
        page: int = 1,
        size: int = 20,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """个股融资融券明细（``DATE`` 倒序）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519``）；须为两融标的，
            非标的返回空列表。
        days:
            取最近 N 个交易日（``0`` = 全部，受 ``page``/``all_pages`` 约束）。
        page / size / all_pages / max_pages:
            分页参数（语义同 :meth:`EastmoneyDataCenterSource.fetch_rows`）。
        """
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        rows = self.fetch_rows(
            filters=[f'scode="{code}"'],
            sort_columns="DATE",
            sort_types="-1",
            page=page,
            size=days if days else size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        if days > 0:
            rows = rows[:days]
        return rows

    # -- 解析 ---------------------------------------------------------------- #
    def parse_margin(self, text: str) -> list[dict[str, Any]]:
        """解析 datacenter JSON 文本 → 归一化行（罐头测试与离线复放用）。"""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "融资融券报表返回非 JSON",
                context={"source": MARGIN, "sample": text[:200]},
                cause=exc,
            ) from exc
        return self._normalize(payload)

    def parse_rows(self, payload: Any) -> list[dict[str, Any]]:
        return self._normalize(payload)

    def _normalize(self, payload: Any) -> list[dict[str, Any]]:
        data = (payload or {}).get("result") or {}
        rows = data.get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            extra = {
                k: r[k]
                for k in r
                if k not in {src for src, _ in _FIELD_MAP}
                and k
                not in (
                    "DATE",
                    "SCODE",
                    "SECNAME",
                    "TRADE_MARKET",
                    "SECUCODE",
                    "TRADE_MARKET_CODE",
                    "KCB",
                    "MARKET",
                )
            }
            out.append(
                {
                    "date": str(r.get("DATE") or "")[:10],
                    "code": str(r.get("SCODE") or ""),
                    "name": str(r.get("SECNAME") or ""),
                    "market": str(r.get("MARKET") or ""),
                    **{dst: _to_f(r.get(src)) for src, dst in _FIELD_MAP},
                    "extra": extra,
                }
            )
        return out
