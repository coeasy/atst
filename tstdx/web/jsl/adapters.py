# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""集思录（可转债 / 分级基金 / ETF）数据源适配器。

从 ``tstdx.web.adapters`` 拆分归组而来，唯一类
:class:`JslSource` 直接继承 :class:`~tstdx.web.base.BaseWebSource`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.models import Quote
from ...errors import SourceDeprecated
from ..base import BaseWebSource
from ..base import num_f as _f
from ..base import num_i as _i
from ..sources import JSL

__all__ = ["JslSource"]


class JslSource(BaseWebSource):
    """集思录（可转债 / 分级基金 / ETF）。

    多数接口需要登录 cookie，未配置时构造即报错（见基类 ``needs_cookie``）。
    """

    BASE = "https://www.jisilu.cn/data/cbnew/cb_list/"
    encoding = "utf-8"  # 集思录 JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return JSL

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return kwargs.get("url") or self.BASE

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "集思录返回非 JSON（可能需要 cookie 或已改版）",
                context={"source": JSL, "sample": text[:200]},
                cause=exc,
            ) from exc
        rows = payload.get("rows") or payload.get("data") or []
        quotes: list[Quote] = []
        for row in rows:
            cell = row.get("cell", row) if isinstance(row, dict) else row
            if not isinstance(cell, Mapping):
                continue
            quotes.append(
                self.normalize_quote(
                    Quote(
                        code=str(cell.get("bond_id") or cell.get("id") or ""),
                        price=_f(cell.get("price") or cell.get("curr_iss_amt")),
                        last_close=_f(cell.get("last_price") or cell.get("pre_price")),
                        volume=_i(cell.get("volume")),
                        amount=_f(cell.get("amount")),
                        extra={"name": cell.get("bond_nm", ""), "raw": dict(cell)},
                    )
                )
            )
        return quotes
