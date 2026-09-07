# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""新浪个股新闻 / 快讯源（自有实现，非复制开源）。

端点
----
个股资讯列表页（HTML）：::

    https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllNewsStock.php
        ?symbol=sh600519&num=20&page=1

该页稳定返回 200，新闻条目以内联结构呈现::

    <br>&nbsp;&nbsp;&nbsp;&nbsp;2026-09-01&nbsp;00:00&nbsp;&nbsp;
    <a target='_blank' href='https://cj.sina.cn/articles/view/...'>标题</a>

解析器按「日期 + 链接」成对提取，链接与前一日期之间可能夹带分类标签
（如 ``<font>研报</font>``），解析时一并剥离并记录为 ``tag``。

说明
----
* 新浪个股新闻 JSON 接口（``stock_news_v3.php`` 等）当前已失效（404/500），
  本源改采稳定的 HTML 资讯页，字段为 ``datetime / title / url / tag``。
* 该页面向 A 股（``sh/sz/bj``）提供；港股 / 美股传入后若页面无数据则返回空列表。
* 仅覆盖「个股新闻」（标题 + 链接 + 时间），新浪未对个股暴露稳定的「快讯」JSON。
* **翻页 / 条数**：新浪服务端忽略 ``page/num`` 参数（恒定返回约 40 条最新），
  故 :meth:`fetch_news` 在客户端按 ``page/num`` 切片分页；``tag`` 过滤同样在
  客户端按解析出的分类标签进行（上游若不携带标签则过滤为空）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .base import BaseWebSource
from .sources import NEWS

# 条目：日期 时间 +（可选标签） + <a href>标题</a>
# 新浪页面用 &nbsp; 充当空白，日期与时间之间也用 &nbsp;，故分隔符需兼容。
_SEP = r"(?:\s|&nbsp;|&#160;)+"
_ITEM_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    + _SEP
    + r"(?P<time>\d{2}:\d{2})"
    + _SEP
    + r"(?P<between>[\s\S]*?)"  # 日期与链接间的分类标签 / 空白（惰性，遇 <a 即止）
    r"<a[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<title>.*?)</a>",
    re.S | re.I,
)
_TAG_RE = re.compile(r"<[^>]+>", re.S)
_WS_RE = re.compile(r"&nbsp;|\s+", re.S)


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", text)).strip()


class SinaNewsSource(BaseWebSource):
    """新浪个股新闻列表（``vCB_AllNewsStock.php``，HTML 解析）。"""

    BASE = "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllNewsStock.php"

    @property
    def source_name(self) -> str:
        return NEWS

    # -- 公用入口 ---------------------------------------------------------- #
    def fetch_news(
        self,
        symbol: str,
        *,
        page: int = 1,
        num: int = 20,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取个股新闻列表。

        Parameters
        ----------
        symbol:
            代码（``600519`` / ``sh600519`` / ``hk00700`` 等）。
        page:
            逻辑页码（从 1 开始）。**注意**：新浪该页服务端忽略
            ``page/num`` 参数、恒定返回约 40 条最新资讯，故 ``page/num``
            由本库在客户端切片实现（见 :meth:`parse_news`）。
        num:
            单页条数上限。
        tag:
            仅取该分类标签的新闻（如 ``"研报"``）；``None`` 不过滤。

        Returns
        -------
        ``[{"datetime": "2026-09-01 00:00", "title": "...",
            "url": "https://...", "tag": "研报" | ""}, ...]``
        """
        from ..domain.symbol import normalize_symbol

        sym = normalize_symbol(symbol)
        url = f"{self.BASE}?symbol={sym}&num={int(num)}&page={int(page)}"
        # 新浪该页为 gb2312/gb18030；统一用 gb18030 解码（兼容 gb2312）
        text = self._request_text(url, encoding="gb18030")
        # 服务端 page/num 不生效，改为客户端切片分页 + tag 过滤
        return self.parse_news(text, tag=tag, page=page, num=num)

    # -- 解析 -------------------------------------------------------------- #
    def _extract_items(self, text: str) -> list[dict[str, Any]]:
        """从 HTML 抽取全部新闻条目（已按 ``(datetime, title)`` 去重）。"""
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for m in _ITEM_RE.finditer(text):
            dt = f"{m.group('date')} {m.group('time')}"
            title = _clean(m.group("title"))
            href = m.group("href").strip()
            between = _clean(m.group("between"))
            tag = "" if between in ("", "-", "·") else between
            if not title or not href:
                continue
            key = (dt, title)
            if key in seen:
                continue
            seen.add(key)
            items.append({"datetime": dt, "title": title, "url": href, "tag": tag})
        return items

    def parse_news(
        self,
        text: str,
        *,
        limit: int | None = None,
        tag: str | None = None,
        page: int | None = None,
        num: int | None = None,
    ) -> list[dict[str, Any]]:
        """解析个股新闻 HTML。

        Parameters
        ----------
        tag:
            仅保留该分类标签的新闻（如 ``"研报"``）。上游页面若不携带分类
            标签，则过滤后恒为空；``None`` 不过滤。
        page, num:
            客户端分页（服务端 ``page/num`` 参数不生效，恒定返回约 40 条
            最新资讯），取第 ``page`` 页、每页 ``num`` 条。
        limit:
            最终条数上限（兼容旧调用）。
        """
        items = self._extract_items(text)
        if not items:
            # 页面无新闻条目（如港股 / 美股未覆盖，或当日无资讯）：返回空列表
            return []
        if tag:
            wanted = {tag} if isinstance(tag, str) else set(tag)
            items = [it for it in items if it["tag"] in wanted]
        if page is not None and num is not None and int(num) > 0:
            pg = max(int(page), 1)
            start = (pg - 1) * int(num)
            items = items[start : start + int(num)]
        if limit is not None:
            items = items[:limit]
        return items

    # -- 基类约定的占位（本源不产出 Quote） ------------------------------- #
    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:  # pragma: no cover
        return self.BASE

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list:  # pragma: no cover
        return []
