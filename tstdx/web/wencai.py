# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""i问财自然语言选股 adapter（接口面吸收，tstdx 自有实现）。

能力面对齐「问财/i问财」语义：自然语言选股查询，返回表头 + 行数据。
但请求/解析路径完全由 tstdx 独立实现，不依赖任何第三方 cookie 服务。

接口事实（公开端点，tstdx 自有抓取与解析）::

    GET https://www.iwencai.com/stockpick/load-data
        ?typed=1&ts=1&f=3&qs=result_rewrite&querytype=stock
        &tid=stockpick&page=1&perpage=50&w=<自然语言条件>
    Header: Cookie: v=<hexin-v>;  hexin-v: <hexin-v>

返回 JSON::

    {"success": true, "data": {"result": {"title": [...], "result": [[...], ...]}}}

设计要点
--------
* **cookie 由调用方注入**（参数 ``cookie`` / 环境变量 ``TSTDX_WENCAI_COOKIE``）。
  tstdx **不**依赖任何第三方 cookie 中继服务——这类服务不可靠且随线下线。
  无 cookie 时抛 :class:`~tstdx.errors.WebSourceError`，引导用户配置。
* **归一化输出**：表头与行 zip 成 ``list[dict]``，便于直接 ``.df`` 落地。
* **罐头可测**：解析逻辑 :meth:`parse_strategy` 是纯函数，fake 响应即可回放。
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from ..errors import WebSourceError
from .base import BaseWebSource
from .sources import WENCAI

__all__ = ["WencaiSource", "WENCAI"]

#: 问财 load-data 端点（公开接口）
_WENCAI_URL = "https://www.iwencai.com/stockpick/load-data"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": (
        "https://www.iwencai.com/stockpick/search?typed=1&preParams=&ts=1&f=3"
        "&qs=result_rewrite&selfsectsn=&querytype=stock&searchfilter="
        "&tid=stockpick&w=&queryarea="
    ),
    "X-Requested-With": "XMLHttpRequest",
}


class WencaiSource(BaseWebSource):
    """i问财自然语言选股查询。

    Examples
    --------
    >>> src = WencaiSource(cookie="ABCD1234...")  # doctest: +SKIP
    >>> rows = src.fetch_strategy("连板3板以上", limit=20)  # doctest: +SKIP
    """

    BASE = _WENCAI_URL

    @property
    def source_name(self) -> str:
        return WENCAI

    def __init__(self, *, cookie: str | None = None, **kwargs: Any) -> None:
        # cookie 由本类自管（基类会以裸值覆盖 Cookie 头，而问财要求 v=<token> 格式）
        token = cookie or os.environ.get("TSTDX_WENCAI_COOKIE", "")
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(_DEFAULT_HEADERS)
        if token:
            headers["Cookie"] = f"v={token}"
            headers["hexin-v"] = token
        super().__init__(headers=headers, **kwargs)
        # 基类 __init__ 会写 self.cookie（None）——必须在 super 之后覆盖
        self.cookie = token

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        query = kwargs.get("query") or (symbols[0] if symbols else "")
        page = int(kwargs.get("page", 1))
        limit = int(kwargs.get("limit", kwargs.get("perpage", 50)))
        params = {
            "typed": 1,
            "ts": 1,
            "f": 3,
            "qs": "result_rewrite",
            "querytype": "stock",
            "tid": "stockpick",
            "page": page,
            "perpage": limit,
            "w": query,
        }
        return f"{self.BASE}?{urlencode(params)}"

    def fetch_strategy(self, query: str, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        """自然语言选股查询。

        Parameters
        ----------
        query:
            自然语言条件，如 ``"连板3板以上"`` / ``"macd金叉 量比大于2"``。
        page, limit:
            分页参数。

        Returns
        -------
        ``list[dict]``，每行一个 dict（key 取自表头）。空结果返回 ``[]``。
        """
        if not query:
            raise WebSourceError("问财查询条件 query 不能为空", context={})
        if not self.cookie:
            raise WebSourceError(
                "i问财需要 cookie：传入 cookie= 参数或设置 TSTDX_WENCAI_COOKIE",
                context={"source": WENCAI},
            )
        url = self.build_url([], query=query, page=page, limit=limit)
        text = self._request_text(url, encoding="utf-8")
        return self.parse_strategy(text)

    def parse_strategy(self, text: str) -> list[dict[str, Any]]:
        """解析问财 load-data JSON 为行 dict 列表。

        - 非 JSON / ``success=False`` → 抛 :class:`WebSourceError`。
        - 空结果 → ``[]``。
        - 表头与行 zip；行长度可能超过表头（多余列丢弃）或不足（缺失列填 ``None``）。
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise WebSourceError(f"问财响应非 JSON: {exc}", context={"source": WENCAI}) from exc
        if not data.get("success", True):
            raise WebSourceError(
                f"问财接口返回异常: {data.get('message', '')}",
                context={"source": WENCAI, "message": data.get("message", "")},
            )
        result = (data.get("data") or {}).get("result") or {}
        titles = result.get("title") or []
        rows = result.get("result") or []
        if not titles or not rows:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            out.append({titles[i]: row[i] if i < len(row) else None for i in range(len(titles))})
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        """BaseWebSource 模板方法兼容（返回 parse_strategy 结果）。"""
        return self.parse_strategy(text)
