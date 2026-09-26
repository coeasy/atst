# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""中国银行外汇牌价数据源适配器。

从 ``tstdx.web.adapters`` 拆分归组而来，唯一类
:class:`BocSource` 直接继承 :class:`~tstdx.web.base.BaseWebSource`。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ...domain.models import Quote
from ..base import BaseWebSource
from ..base import num_f as _f
from ..sources import BOC

__all__ = ["BocSource"]


class BocSource(BaseWebSource):
    """中国银行外汇牌价（HTML 表格）。"""

    BASE = "https://srh.bankofchina.com/search/whpj/search_cn.jsp"
    encoding = "utf-8"  # 中行牌价页为 UTF-8

    @property
    def source_name(self) -> str:
        return BOC

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE

    def fetch_rates(self) -> list[dict[str, Any]]:
        text = self._request_text(self.BASE, encoding="utf-8")
        return self.parse_rates(text)

    @staticmethod
    def parse_rates(html: str) -> list[dict[str, Any]]:
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
        out: list[dict[str, Any]] = []
        for row in rows:
            cells = [
                re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<td.*?>(.*?)</td>", row, re.S)
            ]
            if len(cells) >= 6 and cells[0]:
                out.append(
                    {
                        "currency": cells[0],
                        "buy_rate": _f(cells[1]),
                        "cash_buy_rate": _f(cells[2]),
                        "sell_rate": _f(cells[3]),
                        "cash_sell_rate": _f(cells[4]),
                        "middle_rate": _f(cells[5]),
                        "publish_time": cells[6] if len(cells) > 6 else "",
                    }
                )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        # 汇率不属于 Quote 语义，返回空；请用 fetch_rates()
        return []
