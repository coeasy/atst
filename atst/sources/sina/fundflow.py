# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""新浪资金流历史源（P2-4c/F14：自 ``atst.sources.fundflow`` 按 Provider 归组迁入）。

东财资金流的**交叉验证源**：个股 ``MoneyFlow.ssl_qsfx_zjlrqs`` 与板块
``MoneyFlow.ssl_bkzj_zjlrqs``，接口事实与字段口径随 :class:`SinaFundFlowSource`
的 docstring 搬运（语义未改动）。

旧 import 路径 ``atst.sources.fundflow.SinaFundFlowSource`` 保留同名 re-export
兼容；``providers/sina.py`` 的 ``fund_flow`` 绑定已直指本模块（2026-11-23 续批），
``atst.sources`` 惰性导出表仍经旧路径 re-export 成立。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.models import Quote
from ...errors import SourceDeprecated
from .._scale import DECIMAL_TO_PERCENT
from ..base import BaseWebSource
from ..base import num_f as _f
from ..base import num_i as _i
from ..sources import SINA_FUND_FLOW

__all__ = ["SinaFundFlowSource"]


# --------------------------------------------------------------------------- #
class SinaFundFlowSource(BaseWebSource):
    """新浪资金流历史（个股 / 板块）——东财资金流的**交叉验证源**。

    接口事实（2026-09-02 真实抓包验证，atst 自有实现）::

        GET https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/
            MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=2&sort=opendate&asc=0&daima=sh600519
        [{"opendate":"2026-09-01","trade":"1299.5000",
          "changeratio":"-0.0000153903","turnover":"25.9163",
          "netamount":"-308661986.4400","ratioamount":"-0.0733546",
          "r0_net":"-115750054.9700","r0_ratio":"-0.02750840",
          "r0x_ratio":"-90.0321","cnt_r0x_ratio":"-2",
          "cate_ra":"0.0797202","cate_na":"1400012001.8600"}]

    板块版本 ``MoneyFlow.ssl_bkzj_zjlrqs?...&bankuai=new_dzxx``：字段无
    ``trade``/``changeratio``/``cate_ra``/``cate_na``，改以
    ``avg_price``/``avg_changeratio`` 描述板块均价与涨跌。

    .. warning::
       必须显式传 ``page`` / ``num`` / ``sort`` 三个参数：实测只给 ``daima``
       时接口**忽略分页、返回全历史**（约 1.1 MB）。

    .. note::
       板块代码用 :class:`~atst.sources.boards.SinaIndustryBoardSource` 的
       ``new_xxx`` 体系（如 ``new_dzxx``）；旧 ``hangye_ZLxx`` 体系虽仍响应，
       但数据已停更（最新停在 2020-06-30）。

    .. note::
       ``turnover`` 为新浪原始字段（页面表头写作「换手率」，但个股与板块量纲
       明显不同，官方未公示口径），atst **原值透出**为 ``turnover_raw``，
       不做推测换算。
    """

    _BASE = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    #: 个股资金流历史（daima=sh600519）
    _STOCK_PATH = "MoneyFlow.ssl_qsfx_zjlrqs"
    #: 板块资金流历史（bankuai=new_dzxx）
    _BOARD_PATH = "MoneyFlow.ssl_bkzj_zjlrqs"

    @property
    def source_name(self) -> str:
        return SINA_FUND_FLOW

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = symbols[0] if symbols else kwargs.get("symbol", "")
        return self._url(
            self._STOCK_PATH,
            "daima",
            symbol,
            page=int(kwargs.get("page", 1)),
            size=int(kwargs.get("size", 20)),
        )

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        # 资金流是「按日的历史序列」，不是 Quote 快照，故不产出 Quote。
        return []

    # -- URL --------------------------------------------------------------- #
    def _url(self, path: str, key: str, value: str, *, page: int, size: int) -> str:
        return f"{self._BASE}{path}?page={page}&num={size}&sort=opendate&asc=0&{key}={value}"

    def _fetch_rows(self, url: str) -> list[Mapping[str, Any]]:
        text = self._request_text(url, encoding="gb18030")
        text = text.strip()
        if not text or text in ("null", "[]"):
            return []
        if text.startswith("{"):  # 新浪错误体 {"__ERROR":1,...}
            raise SourceDeprecated(
                f"新浪资金流接口报错: {text[:120]}",
                context={"source": self.source_name, "url": url},
            )
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "新浪资金流响应非 JSON",
                context={"source": self.source_name, "url": url},
                cause=exc,
            ) from exc
        return [r for r in (rows or []) if isinstance(r, Mapping)]

    # -- 公开 API ---------------------------------------------------------- #
    def fetch_stock_flow(
        self,
        symbol: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> list[dict[str, Any]]:
        """个股资金流历史（按日倒序）。

        Parameters
        ----------
        symbol:
            支持 ``600519`` / ``sh600519`` / ``sh.600519`` 等书写变种。
        size:
            每页条数；``sort=opendate&asc=0`` 固定按日期倒序。

        Returns
        -------
        ``[{"date","close","change_pct","turnover_raw","net_amount",
        "net_ratio","super_large_net","super_large_ratio","r0x_ratio",
        "r0x_days","industry_net","industry_ratio"}, ...]``

        金额单位为**元**、比率单位为**百分数**（``-7.34`` 即 -7.34%）。
        """
        from ...domain.symbol import normalize_symbol

        sym = normalize_symbol(symbol)
        url = self._url(self._STOCK_PATH, "daima", sym, page=page, size=size)
        return [self._stock_row(r) for r in self._fetch_rows(url)]

    def fetch_board_flow(
        self,
        board_code: str,
        *,
        page: int = 1,
        size: int = 20,
    ) -> list[dict[str, Any]]:
        """板块资金流历史（按日倒序）。

        Parameters
        ----------
        board_code:
            新浪行业板块代码（``new_dzxx``），取自
            :class:`~atst.sources.boards.SinaIndustryBoardSource` 的 ``code`` 字段。

        Returns
        -------
        ``[{"date","avg_price","avg_change_pct","turnover_raw","net_amount",
        "net_ratio","super_large_net","super_large_ratio","r0x_ratio",
        "r0x_days"}, ...]``

        金额单位为**元**、比率单位为**百分数**。
        """
        url = self._url(self._BOARD_PATH, "bankuai", board_code, page=page, size=size)
        return [self._board_row(r) for r in self._fetch_rows(url)]

    # -- 行解析 ------------------------------------------------------------ #
    @staticmethod
    def _stock_row(r: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "date": str(r.get("opendate") or ""),
            "close": _f(r.get("trade")),
            "change_pct": _pct(r.get("changeratio")),
            "turnover_raw": _f(r.get("turnover")),
            "net_amount": _f(r.get("netamount")),
            "net_ratio": _pct(r.get("ratioamount")),
            "super_large_net": _f(r.get("r0_net")),
            "super_large_ratio": _pct(r.get("r0_ratio")),
            "r0x_ratio": _f(r.get("r0x_ratio")),
            "r0x_days": _i(r.get("cnt_r0x_ratio")),
            "industry_net": _f(r.get("cate_na")),
            "industry_ratio": _pct(r.get("cate_ra")),
        }

    @staticmethod
    def _board_row(r: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "date": str(r.get("opendate") or ""),
            "avg_price": _f(r.get("avg_price")),
            "avg_change_pct": _pct(r.get("avg_changeratio")),
            "turnover_raw": _f(r.get("turnover")),
            "net_amount": _f(r.get("netamount")),
            "net_ratio": _pct(r.get("ratioamount")),
            "super_large_net": _f(r.get("r0_net")),
            "super_large_ratio": _pct(r.get("r0_ratio")),
            "r0x_ratio": _f(r.get("r0x_ratio")),
            "r0x_days": _i(r.get("cnt_r0x_ratio")),
        }


def _pct(value: Any) -> float:
    """新浪 ``changeratio`` 族为**小数**（``-0.0000153903``）→ 百分数。"""
    return round(_f(value) * DECIMAL_TO_PERCENT, 6)
