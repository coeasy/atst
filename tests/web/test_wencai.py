"""i问财自然语言选股 adapter 测试（全部离线罐头，不发起真实 HTTP）。

罐头 JSON 结构对齐 iwencai load-data 真实返回：
``{"success": true, "data": {"result": {"title": [...], "result": [[...]]}}}``
"""

from __future__ import annotations

import json

import pytest

from tstdx.errors import WebSourceError
from tstdx.web.base import HttpResponse, RateLimiter
from tstdx.web.sources import KNOWN_SOURCES
from tstdx.web.wencai import WencaiSource

pytestmark = pytest.mark.unit


class FakeHttp:
    """返回预置响应的假 HTTP 客户端。"""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


#: 罐头：2 行 × 3 列（title 与行 zip）
CANNED = json.dumps(
    {
        "success": True,
        "message": None,
        "data": {
            "result": {
                "title": ["股票代码", "股票简称", "最新价"],
                "result": [
                    ["600519", "贵州茅台", 1690.0],
                    ["000001", "平安银行", 12.34],
                ],
            }
        },
    }
).encode("utf-8")


def _src(body: bytes = CANNED, *, cookie: str = "tok", status: int = 200) -> WencaiSource:
    src = WencaiSource(cookie=cookie, max_retries=0)
    src.client = FakeHttp(body, status=status)
    src.rate_limiter = RateLimiter()
    return src


class TestParseStrategy:
    def test_zip_title_rows(self) -> None:
        rows = WencaiSource(cookie="tok").parse_strategy(CANNED.decode("utf-8"))
        assert rows == [
            {"股票代码": "600519", "股票简称": "贵州茅台", "最新价": 1690.0},
            {"股票代码": "000001", "股票简称": "平安银行", "最新价": 12.34},
        ]

    def test_row_shorter_than_title_fills_none(self) -> None:
        text = json.dumps(
            {
                "success": True,
                "data": {
                    "result": {
                        "title": ["a", "b", "c"],
                        "result": [["1", "2"]],
                    }
                },
            }
        )
        rows = WencaiSource(cookie="tok").parse_strategy(text)
        assert rows == [{"a": "1", "b": "2", "c": None}]

    def test_empty_result(self) -> None:
        text = json.dumps({"success": True, "data": {"result": {}}})
        assert WencaiSource(cookie="tok").parse_strategy(text) == []

    def test_bad_json(self) -> None:
        with pytest.raises(WebSourceError, match="非 JSON"):
            WencaiSource(cookie="tok").parse_strategy("not-json")

    def test_success_false(self) -> None:
        text = json.dumps({"success": False, "message": "被限流"})
        with pytest.raises(WebSourceError, match="被限流"):
            WencaiSource(cookie="tok").parse_strategy(text)


class TestFetchStrategy:
    def test_fetch_returns_rows(self) -> None:
        rows = _src().fetch_strategy("连板3板以上", limit=20)
        assert len(rows) == 2
        assert rows[0]["股票代码"] == "600519"

    def test_url_carries_query_and_pagination(self) -> None:
        src = _src()
        src.fetch_strategy("macd金叉", page=2, limit=30)
        url = src.client.calls[0]
        assert "stockpick/load-data" in url
        assert "page=2" in url and "perpage=30" in url
        assert "w=macd%E9%87%91%E5%8F%89" in url  # urlencode 后的中文条件

    def test_no_cookie_raises(self) -> None:
        src = WencaiSource(cookie=None, max_retries=0)
        with pytest.raises(WebSourceError, match="cookie"):
            src.fetch_strategy("连板")

    def test_empty_query_raises(self) -> None:
        with pytest.raises(WebSourceError, match="query"):
            _src().fetch_strategy("")

    def test_http_403_maps_to_anti_spider(self) -> None:
        from tstdx.errors import AntiSpiderBlocked

        with pytest.raises(AntiSpiderBlocked):
            _src(status=403).fetch_strategy("连板")

    def test_cookie_header_format(self) -> None:
        """Cookie 必须是 ``v=<token>``，且同步携带 hexin-v 头。"""
        src = WencaiSource(cookie="TOK123")
        assert src.headers["Cookie"] == "v=TOK123"
        assert src.headers["hexin-v"] == "TOK123"


class TestRegistry:
    def test_spec_registered(self) -> None:
        spec = KNOWN_SOURCES["wencai"]
        assert spec.capabilities == ("wencai",)
        assert spec.default_rate == 1

    def test_source_name(self) -> None:
        assert WencaiSource(cookie="tok").source_name == "wencai"
