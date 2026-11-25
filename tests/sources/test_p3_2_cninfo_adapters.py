# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3-2 低覆盖模块补离线单测：巨潮公告适配器（sources/cninfo/adapters.py）。

全部离线：注入假 HTTP 客户端（BaseWebSource 的传输接缝），验证公开行为——
column 板块选择、``代码,orgId`` 联想缓存、响应解析、PDF 链接拼装、
互动易问答映射与干净失败——不发起任何真实 HTTP 请求。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources.base import HttpResponse
from atst.sources.cninfo import adapters as cninfo_adapters
from atst.sources.cninfo.adapters import CninfoSource, _market_of, _strip_prefix

pytestmark = pytest.mark.unit

#: 罐头响应：代码联想 + 公告列表（按 URL 关键字路由）
_ANNOUNCE = {
    "announcements": [
        {
            "secCode": "600519",
            "secName": "贵州茅台",
            "announcementTitle": "2025 年年度报告",
            "announcementId": 12345,
            "announcementTime": 1782748800000,  # 毫秒时间戳
            "adjunctUrl": "finalpage/2026-09-30/annual.PDF",
            "adjunctType": "PDF",
            "orgId": "9900000000",
        },
        {"secCode": "600519", "announcementTitle": "无附件公告", "announcementTime": None},
    ]
}
_SEARCH_ROWS = [
    {"code": "600519", "orgId": "9900000000"},
    {"code": "000001", "orgId": "x"},
    {"code": "00700", "orgId": "hk00000"},
]


class _FakeHttpClient:
    """BaseWebSource 传输接缝的假实现：按 canned 响应回放 POST。"""

    def __init__(
        self,
        post_status: int = 200,
        post_body: bytes = json.dumps(_ANNOUNCE).encode(),
        search_status: int = 200,
        search_body: bytes = json.dumps(_SEARCH_ROWS).encode(),
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (url, body_str, content_type)
        self.post_status = post_status
        self.post_body = post_body
        self.search_status = search_status
        self.search_body = search_body

    def post(
        self,
        url: str,
        *,
        headers: Any = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        if "topSearch" in url:
            status, raw = self.search_status, self.search_body
        else:
            status, raw = self.post_status, self.post_body
        self.calls.append((url, (body or b"").decode("utf-8"), content_type or ""))
        return HttpResponse(status=status, body=raw)

    def get(self, url: str, **kwargs: Any) -> HttpResponse:  # pragma: no cover - 本源只用 POST
        raise AssertionError("cninfo 适配器不应发起 GET")


def _make_src(**kw: Any) -> tuple[CninfoSource, _FakeHttpClient]:
    client = _FakeHttpClient(**kw)
    src = CninfoSource(client=client, max_retries=0)
    return src, client


def _form_of(client: _FakeHttpClient, index: int = -1) -> dict[str, str]:
    url, body, _ = client.calls[index]
    return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}


@pytest.fixture(autouse=True)
def _isolated_org_cache(monkeypatch: pytest.MonkeyPatch):
    """orgId 缓存是模块级全局——每个用例都换一张空表，用完恢复。"""
    monkeypatch.setattr(cninfo_adapters, "_ORG_ID_CACHE", {})


class TestMarketHelpers:
    def test_market_of(self) -> None:
        """#1 _market_of：前缀与首位数字判定板块。"""
        assert _market_of("sh600519") == "sh"
        assert _market_of("600519") == "sh"
        assert _market_of("sz000001") == "sz"
        assert _market_of("300750") == "sz"
        assert _market_of("hk00700") == "hk"
        assert _market_of("") == ""
        assert _market_of("XX999") == ""

    def test_strip_prefix(self) -> None:
        """#2 _strip_prefix：sh/sz/bj/hk 前缀剥离。"""
        assert _strip_prefix("sh600519") == "600519"
        assert _strip_prefix("SZ000001") == "000001"
        assert _strip_prefix("bj830799") == "830799"
        assert _strip_prefix("hk00700") == "00700"
        assert _strip_prefix("600519") == "600519"


class TestFetchAnnouncements:
    def test_announcement_parsing(self) -> None:
        """#3 公告解析：标题/URL 前缀/时间串/来源字段齐全。"""
        src, client = _make_src()
        rows = src.fetch_announcements("600519")
        assert [r["title"] for r in rows] == ["2025 年年度报告", "无附件公告"]
        first = rows[0]
        assert first["symbol"] == "600519"
        assert first["name"] == "贵州茅台"
        assert first["url"] == "http://static.cninfo.com.cn/finalpage/2026-09-30/annual.PDF"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", first["publish_time"])
        assert first["file_type"] == "PDF"
        assert first["org_id"] == "9900000000"
        assert first["source"] == "cninfo"
        second = rows[1]
        assert second["url"] == ""  # 无 adjunctUrl → 空 URL
        assert second["publish_time"] == ""  # 非法时间戳 → 诚实留空
        assert second["announcement_id"] == ""

    def test_column_by_market(self) -> None:
        """#4 column 按市场前缀选择：sh→sse、sz→szse、hk→hke、缺省 szse。"""
        src, client = _make_src()
        src.fetch_announcements("600519")
        assert _form_of(client)["column"] == "sse"
        src.fetch_announcements("sz000001")
        assert _form_of(client)["column"] == "szse"
        src.fetch_hk_announcements("hk00700")
        assert _form_of(client)["column"] == "hke"
        src.fetch_announcements(None)
        assert _form_of(client)["column"] == "szse"  # 默认板块
        assert "stock" not in _form_of(client)  # 无标的时不带 stock 参数

    def test_stock_param_requires_org_id(self) -> None:
        """#5 查公告必须带 ``代码,orgId``：联想结果为空时干净失败。"""
        src, client = _make_src(search_body=json.dumps([{"code": "999999", "orgId": ""}]).encode())
        with pytest.raises(WebSourceError, match="orgId"):
            src.fetch_announcements("600519")

    def test_org_id_cache_hit_and_eviction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#6 orgId 进程内缓存：命中免联想；容量满按插入序淘汰最旧。"""
        src, client = _make_src()
        src.fetch_announcements("600519")
        src.fetch_announcements("sh600519")
        searches = [c for c in client.calls if "topSearch" in c[0]]
        assert len(searches) == 1  # 第二次命中缓存，不再联想
        assert cninfo_adapters._ORG_ID_CACHE["600519"] == "9900000000"

        monkeypatch.setattr(cninfo_adapters, "_ORG_ID_CACHE_CAPACITY", 2)
        monkeypatch.setattr(
            cninfo_adapters,
            "_ORG_ID_CACHE",
            {"111111": "a", "222222": "b"},
        )
        src.fetch_announcements("sz000001")  # 容量=2 已满：淘汰最早进入的 111111
        assert "111111" not in cninfo_adapters._ORG_ID_CACHE
        assert cninfo_adapters._ORG_ID_CACHE.get("222222") == "b"
        assert cninfo_adapters._ORG_ID_CACHE.get("000001") == "x"
        assert len(cninfo_adapters._ORG_ID_CACHE) == 2

    def test_date_range_and_limit_clamp(self) -> None:
        """#7 seDate 区间拼装与 pageSize 上限 30 钳制。"""
        src, client = _make_src()
        rows = src.fetch_announcements(
            "600519", limit=99, start_date="2026-01-01", end_date="2026-09-30"
        )
        form = _form_of(client)
        assert form["seDate"] == "2026-01-01~2026-09-30"
        assert form["pageSize"] == "30"
        assert len(rows) <= 30
        src.fetch_announcements("600519", limit=5)
        assert _form_of(client)["pageSize"] == "5"

    def test_non_json_response(self) -> None:
        """#8 公告响应非 JSON：计一次解析失败并抛 ValueError。"""
        src, client = _make_src(post_body=b"<html>not json</html>")
        with pytest.raises(ValueError, match="非 JSON"):
            src.fetch_announcements("600519")
        assert src._parse_failures == 1  # noqa: SLF001 - 验证失败桶语义

    def test_parse_method(self) -> None:
        """#9 fetch 模板方法的 parse 出口。"""
        src, _ = _make_src()
        rows = src.parse(json.dumps(_ANNOUNCE), ["600519"])
        assert len(rows) == 2 and rows[0]["source"] == "cninfo"


class TestInteractiveQa:
    _QA_PAYLOAD = {
        "data": {
            "questionList": [
                {
                    "secCode": "600519",
                    "questionContent": "分红计划？",
                    "answerContent": "详见公告。",
                    "createDate": "2026-09-01",
                    "replyDate": "2026-09-02",
                    "orgId": "9900000000",
                },
                "not-a-mapping",
                {
                    "stockCode": "000001",
                    "question": "q2",
                    "answer": "a2",
                    "askTime": "2026-09-03",
                    "replyTime": "2026-09-04",
                },
            ]
        }
    }

    def test_qa_parsing_with_fallback_keys(self) -> None:
        """#10 互动易问答：data.questionList 主路径 + 字段回退键 + 非映射行跳过。"""
        src, client = _make_src(post_body=json.dumps(self._QA_PAYLOAD).encode())
        rows = src.fetch_interactive_qa(symbol="600519", keyword="分红", page=2, size=99)
        assert len(rows) == 2
        assert rows[0] == {
            "symbol": "600519",
            "question": "分红计划？",
            "answer": "详见公告。",
            "ask_time": "2026-09-01",
            "reply_time": "2026-09-02",
            "org_id": "9900000000",
            "source": "cninfo_irm",
        }
        assert rows[1]["symbol"] == "000001" and rows[1]["question"] == "q2"
        form = _form_of(client)
        assert form["pageNo"] == "2"
        assert form["pageSize"] == "50"  # 单页上限 50 钳制
        assert form["keyWord"] == "分红"

    def test_qa_no_symbol_no_keyword(self) -> None:
        """#11 无 symbol/keyword：不带 orgId/keyWord，顶层 questionList 回退路径。"""
        payload = {"questionList": [{"secCode": "600519", "questionContent": "q"}]}
        src, client = _make_src(post_body=json.dumps(payload).encode())
        rows = src.fetch_interactive_qa()
        form = _form_of(client)
        assert "orgId" not in form and "keyWord" not in form
        assert rows[0]["question"] == "q"

    def test_qa_endpoint_failure_wraps_cleanly(self) -> None:
        """#12 端点失效：包装为 WebSourceError（干净失败），不裸抛传输异常。"""
        src, _ = _make_src(post_status=500, post_body=b"error")
        with pytest.raises(WebSourceError, match="needs_verify"):
            src.fetch_interactive_qa(symbol="600519")
