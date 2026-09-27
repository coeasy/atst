# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""个股新闻 / 财经快讯 / 机构调研源（自有实现，非复制开源）。

模块包含两类数据源：

* :class:`SinaNewsSource` —— 新浪个股新闻（HTML 页解析，按代码取个股资讯）。
* :class:`EastmoneyNewsSource` —— 东方财富财经快讯（全市场滚动头条，对标
  niuniu ``/api/news/financial`` 新浪财经头条）。
* :class:`EastmoneyResearchVisitSource` —— 东方财富机构调研（调研纪要，
  对标 niuniu ``/api/news/research-visits/{code}``）。

个股新闻端点（新浪）
------------------
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
  故 :meth:`SinaNewsSource.fetch_news` 在客户端按 ``page/num`` 切片分页；
  ``tag`` 过滤同样在客户端按解析出的分类标签进行（上游若不携带标签则过滤为空）。
* 财经快讯 / 机构调研为东财后端，复用 :class:`~atst.web._base_em._EastmoneyJson`
  主机池 failover；机构调研报表名为 best-effort，需重新抓包校准。
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ._base_em import _EastmoneyJson
from .base import BaseWebSource
from .corporate import EastmoneyDataCenterSource
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


# --------------------------------------------------------------------------- #
# 财经快讯（全市场滚动头条）
# --------------------------------------------------------------------------- #
#: 财经快讯主机（独立主机，单元素池）
_NEWS_HOSTS = ("https://newsapi.eastmoney.com",)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class EastmoneyNewsSource(_EastmoneyJson):
    """东方财富财经快讯（滚动资讯头条）。

    对标 niuniu ``/api/news/financial``（原新浪财经头条）。返回最新财经快讯
    列表，含标题 / 摘要 / 正文 / 发布时间 / 标签。

    Quick start::

        from atst.web.news import EastmoneyNewsSource
        src = EastmoneyNewsSource()
        headlines = src.fetch_news(page=1, size=30)   # -> list[dict]
        src.close()
    """

    #: 快讯为独立主机（非 push2/datacenter 系），单元素主机池
    HOSTS = _NEWS_HOSTS
    JSON_LABEL = "财经快讯"

    @property
    def source_name(self) -> str:
        return "eastmoney"

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("EastmoneyNewsSource 为数据型源，请使用 fetch_news")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def fetch_news(self, *, page: int = 1, size: int = 30) -> list[dict[str, Any]]:
        """拉取财经快讯列表（按时间倒序）。

        Parameters
        ----------
        page, size:
            分页参数；``size`` 上限 100。

        Returns
        -------
        ``[{"id","title","content","summary","time","url","labels"}, ...]``
        """
        ts = int(time.time() * 1000)
        path = (
            f"/kuaixun/v1/getlist?type=1&page_index={max(1, page)}"
            f"&page_size={max(1, min(size, 100))}&_={ts}"
        )
        payload = self._get_json(path)
        data = payload.get("data") or {}
        rows = data.get("list") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            # 时间字段兼容 ctime（unix 秒） / update_time（字符串）
            ctime = r.get("ctime")
            if isinstance(ctime, (int, float)) and ctime > 0:
                stamp = str(int(ctime * 1000)) if ctime < 1e12 else str(int(ctime))
            else:
                stamp = _s(r.get("update_time") or r.get("time") or ctime)
            out.append(
                {
                    "id": _s(r.get("id")),
                    "title": _s(r.get("title") or r.get("digest")),
                    "content": _s(r.get("content") or r.get("summary")),
                    "summary": _s(r.get("summary")),
                    "time": stamp,
                    "url": _s(r.get("url")),
                    "labels": _s(r.get("labels")),
                }
            )
        return out


# --------------------------------------------------------------------------- #
# 机构调研（调研纪要）
# --------------------------------------------------------------------------- #
class EastmoneyResearchVisitSource(EastmoneyDataCenterSource):
    """东方财富机构调研（调研纪要 / 接待记录）。

    对标 niuniu ``/api/news/research-visits/{code}``（原巨潮 CNINFO 调研）。
    基于 datacenter-web 报表 ``RPT_ORG_SURVEY_DET``（best-effort 报表名）。

    Quick start::

        from atst.web.news import EastmoneyResearchVisitSource
        src = EastmoneyResearchVisitSource()
        visits = src.fetch_visits("000001", page=1, size=20)  # -> list[dict]
        src.close()
    """

    JSON_LABEL = "机构调研"

    def __init__(self, **kwargs: Any) -> None:
        # 报表名经参数传递（避免运行时替换实例属性——线程不安全）
        super().__init__("RPT_ORG_SURVEY_DET", **kwargs)

    def fetch_visits(self, symbol: str, *, page: int = 1, size: int = 20) -> list[dict[str, Any]]:
        """拉取个股机构调研记录（按调研日期倒序）。

        Returns
        -------
        ``[{"code","name","date","org","type","summary","content"}, ...]``
        """
        rows = self.fetch_rows_by_code(
            symbol,
            sort_columns="RECEIVED_DATE",
            sort_types="-1",
            page=page,
            size=size,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append(
                {
                    "code": _s(r.get("SECURITY_CODE")),
                    "name": _s(r.get("SECURITY_NAME_ABBR") or r.get("SECURITY_NAME")),
                    "date": _s(r.get("RECEIVED_DATE")),
                    "org": _s(r.get("ORG_NAME")),
                    "type": _s(r.get("RESEARCH_TYPE")),
                    "summary": _s(r.get("SURVEY_SUMMARY")),
                    "content": _s(r.get("CONTENT") or r.get("SURVEY_CONTENT")),
                }
            )
        return out
