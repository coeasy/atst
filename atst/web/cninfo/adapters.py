# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""巨潮资讯网（cninfo）法定披露公告适配器。

端点（2026-10-02 真机验证）
--------------------------
``POST http://www.cninfo.com.cn/new/hisAnnouncement/query``（表单编码）

``column`` 决定板块：

* ``szse`` —— 深市（主板 + 创业板）
* ``sse``  —— 沪市（主板 + 科创板）
* ``hke``  —— 港交所

为什么不走 ``webapi.cninfo.com.cn``：那套（``p_info3015`` 等）返回
``{"resultcode":401,"resultmsg":"未经授权的访问,code_005_ipban_notoken"}``，
需要申请 token；本适配器只用**无需凭据**的公开披露查询接口。

未收录：北交所（``column=bse``）实测 ``totalRecordNum=0``，口径未确认，
按「不留死能力」纪律不登记 ``bse_announcements``。
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any

from ...errors import WebSourceError
from ..base import BaseWebSource
from ..sources import CNINFO

__all__ = ["CninfoSource"]

#: 公告 PDF 静态前缀（响应 ``adjunctUrl`` 为相对路径）
_STATIC_PREFIX = "http://static.cninfo.com.cn/"

#: 市场前缀 → 巨潮 column
_COLUMN_BY_MARKET = {"sh": "sse", "sz": "szse", "hk": "hke"}

#: 未提供标的时的默认板块（覆盖深市全市场）
_DEFAULT_COLUMN = "szse"

#: 代码联想端点：巨潮查询公告必须给 ``stock=代码,orgId``（**只给代码返回 0 条**，
#: 实测 ``stock=000001`` → totalRecordNum=0，``stock=000001,gssz0000001`` → 1997）。
_SEARCH_URL = "http://www.cninfo.com.cn/new/information/topSearch/query"

#: 代码 → orgId 的进程内查表结果（orgId 不随时间变化，省掉每次公告查询前的一次联想请求）。
#: 它只记 orgId 这一项元数据，**不存任何行情/公告结果**——复用的是「代码查 orgId」
#: 这一步的往返，不是把响应留下来。
_ORG_ID_CACHE: dict[str, str] = {}


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class CninfoSource(BaseWebSource):
    """巨潮资讯网法定披露公告源。"""

    BASE = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    encoding = "utf-8"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault(
            "headers",
            {
                "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        super().__init__(**kwargs)

    @property
    def source_name(self) -> str:
        return CNINFO

    def build_url(self, symbols: Any, **kwargs: Any) -> str:
        """``fetch`` 模板方法入口（本源实际走 POST，这里返回查询基址）。"""
        return self.BASE

    def parse(self, text: str, symbols: Any, **kwargs: Any) -> list[Any]:
        return self._parse_payload(json.loads(text))

    # -- 能力出口（executor 按 binding.method 分派） ------------------------ #

    def fetch_announcements(
        self,
        symbol: str | None = None,
        *,
        limit: int = 30,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """A 股法定披露公告列表。

        Parameters
        ----------
        symbol:
            ``600519`` / ``sh600519`` / ``sz000001`` 均可；为空则取深市全市场。
        limit:
            返回条数（巨潮侧为 ``pageSize``，上限 30/页，本方法按页内截断）。
        start_date / end_date:
            ``YYYY-MM-DD``；任一提供则拼 ``seDate`` 区间。
        """
        column = _COLUMN_BY_MARKET.get(_market_of(symbol), _DEFAULT_COLUMN)
        return self._query(
            column, symbol=symbol, limit=limit, start_date=start_date, end_date=end_date
        )

    def fetch_hk_announcements(
        self,
        symbol: str | None = None,
        *,
        limit: int = 30,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """港交所披露公告列表（``column=hke``）。"""
        return self._query(
            "hke", symbol=symbol, limit=limit, start_date=start_date, end_date=end_date
        )

    # -- 互动易 / e 互动问答（G-07，best-effort，needs_verify） ------------ #
    _IRM_URL = "http://irm.cninfo.com.cn/ircs/question/getQuestionList"

    def fetch_interactive_qa(
        self,
        *,
        symbol: str = "",
        keyword: str = "",
        page: int = 1,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """互动易 / e 互动投资者问答（巨潮 IRM 平台，best-effort 端点）。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀；为空则按 ``keyword`` 全局搜索。
        keyword:
            问题关键词；二者可任选其一或同时给。
        page, size:
            分页（巨潮 IRM 单页上限约 50）。

        Returns
        -------
        ``[{"symbol","question","answer","ask_time","reply_time","org_id"}, ...]``

        .. note::
            巨潮 IRM 端点为 best-effort，待真机抓包校准（``needs_verify``）；
            端点失效时抛 :class:`~atst.errors.WebSourceError`（干净失败）。
        """
        self._check_deprecated()
        self.rate_limiter.acquire(self.source_name)
        org_id = ""
        if symbol:
            param = self._stock_param(symbol)
            org_id = param.split(",")[-1] if "," in param else ""
        form: dict[str, str] = {
            "pageNo": str(max(1, page)),
            "pageSize": str(max(1, min(int(size), 50))),
        }
        if org_id:
            form["orgId"] = org_id
        if keyword:
            form["keyWord"] = keyword
        try:
            text = self._request_post(
                self._IRM_URL,
                urllib.parse.urlencode(form).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
                encoding="utf-8",
                err_msg="巨潮互动易请求失败",
            )
            payload = json.loads(text)
        except (ValueError, WebSourceError) as exc:
            raise WebSourceError(
                "巨潮互动易端点未校准或不可达（needs_verify）",
                context={"source": CNINFO, "symbol": symbol},
                cause=exc,
            ) from exc
        return self._parse_qa(payload)

    @staticmethod
    def _parse_qa(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = (payload.get("data") or {}).get("questionList") or payload.get("questionList") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append(
                {
                    "symbol": _s(r.get("secCode") or r.get("stockCode") or ""),
                    "question": _s(r.get("questionContent") or r.get("question")),
                    "answer": _s(r.get("answerContent") or r.get("answer")),
                    "ask_time": _s(r.get("createDate") or r.get("askTime")),
                    "reply_time": _s(r.get("replyDate") or r.get("replyTime")),
                    "org_id": _s(r.get("orgId")),
                    "source": "cninfo_irm",
                }
            )
        return out

    # -- 内部 -------------------------------------------------------------- #

    def _query(
        self,
        column: str,
        *,
        symbol: str | None,
        limit: int,
        start_date: str | None,
        end_date: str | None,
    ) -> list[dict[str, Any]]:
        self._check_deprecated()
        self.rate_limiter.acquire(self.source_name)

        form: dict[str, str] = {
            "pageNum": "1",
            "pageSize": str(max(1, min(int(limit), 30))),
            "column": column,
            "tabName": "fulltext",
        }
        if symbol:
            form["stock"] = self._stock_param(symbol)
        if start_date or end_date:
            form["seDate"] = f"{start_date or ''}~{end_date or ''}"

        text = self._request_post(
            self.BASE,
            urllib.parse.urlencode(form).encode("utf-8"),
            content_type="application/x-www-form-urlencoded",
            encoding="utf-8",
            err_msg="巨潮公告查询请求失败",
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._record_failure(parse=True)
            raise ValueError(f"巨潮公告响应非 JSON: {text[:120]!r}") from exc
        return self._parse_payload(payload)[:limit]

    def _stock_param(self, symbol: str) -> str:
        """查公告必须的 ``代码,orgId`` 形式（只给代码时巨潮返回 0 条）。"""
        code = _strip_prefix(symbol)
        org_id = _ORG_ID_CACHE.get(code)
        if org_id is None:
            org_id = self._lookup_org_id(code)
            if not org_id:
                #: 查不到 orgId 就不要发「注定返回 0 条」的请求——那会让调用方
                #: 把「查不到这家公司」误读成「这家公司最近没有公告」。
                raise WebSourceError(
                    f"巨潮未收录该代码的 orgId，无法按标的查公告：{code}",
                    context={"source": CNINFO, "symbol": symbol},
                )
            _ORG_ID_CACHE[code] = org_id
        return f"{code},{org_id}"

    def _lookup_org_id(self, code: str) -> str:
        body = urllib.parse.urlencode({"keyWord": code, "maxNum": "5"}).encode("utf-8")
        try:
            text = self._request_post(
                _SEARCH_URL,
                body,
                content_type="application/x-www-form-urlencoded",
                encoding="utf-8",
                err_msg="巨潮代码联想请求失败",
            )
            rows = json.loads(text)
        except (ValueError, WebSourceError):
            return ""
        if not isinstance(rows, list):
            return ""
        for row in rows:
            if str(row.get("code") or "") == code and row.get("orgId"):
                return str(row["orgId"])
        return ""

    @staticmethod
    def _parse_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = payload.get("announcements") or []
        out: list[dict[str, Any]] = []
        for item in rows:
            ts = item.get("announcementTime")
            out.append(
                {
                    "symbol": item.get("secCode") or "",
                    "name": item.get("secName") or "",
                    "title": item.get("announcementTitle") or "",
                    "announcement_id": str(item.get("announcementId") or ""),
                    "publish_time": _ms_to_iso(ts),
                    "url": f"{_STATIC_PREFIX}{item['adjunctUrl']}"
                    if item.get("adjunctUrl")
                    else "",
                    "file_type": item.get("adjunctType") or "",
                    "org_id": item.get("orgId") or "",
                    "source": "cninfo",
                }
            )
        return out


def _market_of(symbol: str | None) -> str:
    if not symbol:
        return ""
    s = str(symbol).strip().lower()
    if s.startswith("hk"):
        return "hk"
    if s.startswith("sh") or s.startswith("6"):
        return "sh"
    if s.startswith("sz") or s.startswith(("0", "3")):
        return "sz"
    return ""


def _strip_prefix(symbol: str) -> str:
    """去掉 ``sh``/``sz``/``bj``/``hk`` 市场前缀，返回纯代码。"""
    s = str(symbol).strip().lower()
    for prefix in ("sh", "sz", "bj", "hk"):
        if s.startswith(prefix):
            return s[len(prefix) :]
    return s


def _ms_to_iso(ms: Any) -> str:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))
