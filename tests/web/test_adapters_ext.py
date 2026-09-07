"""扩展 Web 适配器测试：分钟 K 线 / 当日分时 / 代码联想。

罐头响应取自真实接口抓包样本（2026-08 验证），全部离线，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from tstdx.domain.models import Bar, MinutePoint
from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.adapters_ext import (
    MinuteKlineSource,
    MinuteSource,
    SuggestSource,
)
from tstdx.web.base import HttpResponse, RateLimiter


class FakeHttp:
    """返回预置响应的假 HTTP 客户端。"""

    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(200, self.body, {})

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 分钟 K 线
# --------------------------------------------------------------------------- #
class TestMinuteKline:
    # 真实抓包样本：行 = [dt, open, close, high, low, vol(手), {}, ?]
    CANNED = json.dumps(
        {
            "code": 0,
            "data": {
                "qt": {},
                "m5": [
                    [
                        "202608311000",
                        "1690.00",
                        "1691.50",
                        "1692.00",
                        "1689.80",
                        "1234.50",
                        {},
                        "12.34",
                    ],
                    [
                        "202608311005",
                        "1691.50",
                        "1692.00",
                        "1693.00",
                        "1691.00",
                        "456.00",
                        {},
                        "4.56",
                    ],
                ],
            },
        }
    ).encode("utf-8")

    def _src(self) -> MinuteKlineSource:
        src = MinuteKlineSource(max_retries=0)
        src.client = FakeHttp(self.CANNED)
        src.rate_limiter = RateLimiter()
        return src

    def test_fetch_bars(self):
        """分钟 K 线解析 + 手→股归一。"""
        bars = self._src().fetch_bars("600519", period="5min")
        assert len(bars) == 2
        assert all(isinstance(b, Bar) for b in bars)
        assert bars[0].open == 1690.0 and bars[0].close == 1691.5
        assert bars[0].volume == 123450  # 1234.5 手 → 股
        assert bars[1].volume == 45600

    def test_url_uses_mkline_path(self):
        """URL 路径为 /kline/mkline，周期同时在路径与参数。"""
        src = self._src()
        src.fetch_bars("sh600519", period="m15", count=10)
        url = src.client.calls[0]
        assert "/kline/mkline?" in url
        assert "sh600519,m15,,,10" in url

    def test_bad_payload(self):
        """data 缺失 / 非法 JSON → SourceDeprecated。"""
        src = MinuteKlineSource(max_retries=0)
        src.client = FakeHttp(b"not-json")
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch_bars("600519")


class TestMinuteKlineExternal:
    """腾讯 mkline 对港股/美股返回 code=-1 空数据：分钟 K 线该源不支持。

    符号归一（hk00700/usAAPL）本身正确，但后端不提供外部市场分钟线，
    故须抛出明确、可引导的错误而非静默返回空列表。
    """

    HK_EMPTY = json.dumps({"code": -1, "msg": "", "data": {}}).encode("utf-8")
    US_EMPTY = json.dumps({"code": -1, "msg": "", "data": {}}).encode("utf-8")

    def _src(self, body: bytes) -> MinuteKlineSource:
        src = MinuteKlineSource(max_retries=0)
        src.client = FakeHttp(body)
        src.rate_limiter = RateLimiter()
        return src

    def test_hk_raises_clear_error(self):
        with pytest.raises(WebSourceError, match="港股/美股"):
            self._src(self.HK_EMPTY).fetch_bars("hk00700", period="m5")

    def test_us_raises_clear_error(self):
        with pytest.raises(WebSourceError, match="港股/美股"):
            self._src(self.US_EMPTY).fetch_bars("usAAPL", period="m5")

    def test_a_volume_hand_to_shares(self):
        """A 股腾讯量返回「手」，须 ×100 到股。"""
        bars = self._src(TestMinuteKline.CANNED).fetch_bars("sh600519", period="m5")
        assert bars[0].volume == 123450  # 1234.5 手 → 股
        assert bars[1].volume == 45600

    def test_volume_scale_market_aware(self):
        """防御性：若未来放开 hk/us 分钟线（量为股），不应再 ×100。

        以 A 股罐头样本、强制 hk 前缀验证缩放分支（vol_scale=1）。
        """
        bars = self._src(TestMinuteKline.CANNED).parse_bars(
            TestMinuteKline.CANNED.decode("utf-8"), "hk00700", period="m5"
        )
        assert bars[0].volume == 1234  # 1234.50 股，非 123450


# --------------------------------------------------------------------------- #
# 当日分时
# --------------------------------------------------------------------------- #
class TestMinute:
    # 真实抓包样本：每点 "HHMM 价格 累计量(手) 累计额(元)"
    CANNED = json.dumps(
        {
            "code": 0,
            "data": {
                "sh600519": {
                    "data": {
                        "date": "20260831",
                        "data": [
                            "0930 1690.00 12 35045.00",
                            "0931 1691.00 22 112040.00",
                            "0932 1690.50 32 162040.00",
                        ],
                    }
                }
            },
        }
    ).encode("utf-8")

    def _src(self) -> MinuteSource:
        src = MinuteSource(max_retries=0)
        src.client = FakeHttp(self.CANNED)
        src.rate_limiter = RateLimiter()
        return src

    def test_cumulative_diff(self):
        """累计量/额差分还原为分钟增量（额已是元）。"""
        pts = self._src().fetch_minute("600519")
        assert len(pts) == 3
        assert all(isinstance(p, MinutePoint) for p in pts)
        assert pts[0].time == "20260831 09:30"
        assert pts[0].volume == 12 * 100  # 手 → 股
        assert pts[1].volume == 10 * 100  # 差分
        assert pts[2].volume == 10 * 100
        assert pts[1].amount == pytest.approx(112040.00 - 35045.00)
        assert pts[2].amount == pytest.approx(162040.00 - 112040.00)
        assert pts[0].price == 1690.0

    def test_empty(self):
        """空数据 → 空列表。"""
        src = MinuteSource(max_retries=0)
        src.client = FakeHttp(json.dumps({"code": 0, "data": {}}).encode())
        src.rate_limiter = RateLimiter()
        assert src.fetch_minute("600519") == []


# --------------------------------------------------------------------------- #
# 代码联想
# --------------------------------------------------------------------------- #
class TestSuggest:
    # 真实抓包样本：分号分隔记录；列 = 名称,类型码,代码,带市场代码,...
    CANNED = (
        'var suggestvalue="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99,1,,,;'
        "永茂泰,11,605208,sh605208,永茂泰,,永茂泰,99,1,,,;"
        '平安银行,11,000001,sz000001,平安银行,,平安银行,99,1,,,";'
    ).encode("gbk")

    def _src(self) -> SuggestSource:
        src = SuggestSource(max_retries=0)
        src.client = FakeHttp(self.CANNED)
        src.rate_limiter = RateLimiter()
        return src

    def test_parse(self):
        """真实格式解析：名称/代码/市场/规范 symbol。"""
        out = self._src().fetch_suggest("maotai")
        assert len(out) == 3
        assert out[0] == {
            "code": "600519",
            "name": "贵州茅台",
            "market": "sh",
            "symbol": "sh600519",
        }
        assert out[1]["code"] == "605208"
        assert out[2] == {
            "code": "000001",
            "name": "平安银行",
            "market": "sz",
            "symbol": "sz000001",
        }

    def test_limit(self):
        out = self._src().fetch_suggest("maotai", limit=1)
        assert len(out) == 1

    def test_empty_body(self):
        src = SuggestSource(max_retries=0)
        src.client = FakeHttp('var suggestvalue="";'.encode("gbk"))
        src.rate_limiter = RateLimiter()
        assert src.fetch_suggest("zzzz") == []
