"""百度财经源（B0）测试。

罐头响应取自真实接口抓包样本（2026-09-03 验证），全部离线，不发起真实 HTTP。
重点覆盖口径坑：K 线 volume/amount 字段名与语义相反、分时量/额单位换算。
"""

from __future__ import annotations

import json

import pytest

from tstdx.domain.models import Bar, MinutePoint, Quote, Tick
from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.baidu.adapters import BaiduSource
from tstdx.web.base import HttpResponse, RateLimiter
from tstdx.web.sources import BAIDU, get_source, list_sources


class FakeHttp:
    """返回预置响应（按调用次数轮换 body）的假 HTTP 客户端。"""

    def __init__(self, *bodies: bytes):
        self.bodies = list(bodies)
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        body = self.bodies[min(len(self.calls) - 1, len(self.bodies) - 1)]
        return HttpResponse(200, body, {})

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 罐头数据（真实抓包 2026-09-03，茅台 600519）
# --------------------------------------------------------------------------- #
KLINE_5 = {
    "QueryID": "x",
    "ResultCode": "0",
    "Result": [
        {
            "date": "20260828",
            "time": "1787846400",
            "kline": {
                "open": "1289.00",
                "high": "1297.89",
                "low": "1288.00",
                "close": "1297.40",
                "volume": "2086008422.00",
                "amount": "16126",
                "preClose": "1296.20",
                "netChangeRatio": "+0.09",
                "turnoverratio": "0.13",
                "increase": "+1.20",
                "holdingAmount": "0",
            },
            "ma5": {"volume": "1", "avgPrice": "1300.23"},
            "ma10": {"volume": "1", "avgPrice": "1301.10"},
            "ma20": {"volume": "1", "avgPrice": "1295.00"},
        },
        {
            "date": "20260831",
            "time": "1788019200",
            "kline": {
                "open": "1297.99",
                "high": "1305.00",
                "low": "1286.00",
                "close": "1299.52",
                "volume": "3003033720.00",
                "amount": "23248",
                "preClose": "1297.40",
                "netChangeRatio": "+0.16",
                "turnoverratio": "0.18",
                "increase": "+2.12",
                "holdingAmount": "0",
            },
            "ma5": {"volume": "1", "avgPrice": "1299.20"},
            "ma10": {"volume": "1", "avgPrice": "1299.90"},
            "ma20": {"volume": "1", "avgPrice": "1296.00"},
        },
    ],
}

MINUTE = {
    "Result": {
        "basicinfos": {
            "exchange": "sh",
            "code": "600519",
            "name": "贵州茅台",
            "stockStatus": "0",
            "stock_market_code": "sh600519",
        },
        "priceinfo": [
            {
                "time": "1788399000",
                "price": "1297.50",
                "ratio": "+0.00%",
                "increase": "+0.00",
                "volume": "105",
                "avgPrice": "1297.50",
                "amount": "136.25万",
                "totalVolume": "10500",
                "totalAmount": "13625000",
                "timeKey": "0930",
                "datetime": "09-03 09:30",
                "oriAmount": "13625000",
                "show": "1",
            },
            {
                "time": "1788399060",
                "price": "1298.00",
                "ratio": "+0.04%",
                "increase": "+0.50",
                "volume": "200",
                "avgPrice": "1297.75",
                "amount": "259.55万",
                "totalVolume": "30500",
                "totalAmount": "39580000",
                "timeKey": "0931",
                "datetime": "09-03 09:31",
                "oriAmount": "39580000",
                "show": "1",
            },
        ],
        "buyinfos": [
            {"bidprice": "1298.71", "bidvolume": "5000"},
            {"bidprice": "1298.70", "bidvolume": "300"},
        ],
        "askinfos": [
            {"askprice": "1299.19", "askvolume": "2000"},
            {"askprice": "1299.20", "askvolume": "400"},
        ],
        "detailinfos": [
            {
                "time": "1788418032",
                "volume": "700",
                "price": "1298.96",
                "type": "0",
                "bsFlag": "S",
                "formatTime": "14:46",
            },
            {
                "time": "1788418033",
                "volume": "120",
                "price": "1299.00",
                "type": "0",
                "bsFlag": "B",
                "formatTime": "14:46",
            },
            {
                "time": "1788418034",
                "volume": "50",
                "price": "1298.99",
                "type": "0",
                "bsFlag": "S",
                "formatTime": "14:47",
            },
        ],
        "cur": {
            "time": "1788422400",
            "price": "1298.88",
            "ratio": "+0.10%",
            "increase": "+1.38",
            "volume": "17748",
            "avgPrice": "1297.50",
            "amount": "2305193119",
            "totalVolume": "1774765",
            "totalAmount": "2305193119",
            "timeKey": "1500",
        },
        "provider": "东方财富",
    },
}


def _kline_body() -> bytes:
    return json.dumps(KLINE_5).encode("utf-8")


def _minute_body() -> bytes:
    return json.dumps(MINUTE).encode("utf-8")


def _src(body: bytes) -> BaiduSource:
    src = BaiduSource(max_retries=0)
    src.client = FakeHttp(body)
    src.rate_limiter = RateLimiter()
    return src


def _gen_kline(n: int, start_ts: int, step: int = 86400) -> dict:
    """合成 n 根日K（time 从 start_ts 递增，日期全局唯一），用于分页测试。"""
    rows = []
    for i in range(n):
        ts = start_ts + i * step
        rows.append(
            {
                "date": f"D{ts}",
                "time": str(ts),
                "kline": {
                    "open": "10.00",
                    "high": "11.00",
                    "low": "9.00",
                    "close": "10.50",
                    "volume": "1000000.00",
                    "amount": "100",
                    "preClose": "10.00",
                    "netChangeRatio": "+0.09",
                    "turnoverratio": "0.13",
                    "increase": "+0.50",
                    "holdingAmount": "0",
                },
            }
        )
    return {"QueryID": "x", "ResultCode": "0", "Result": rows}


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
class TestRegistry:
    def test_source_registered(self):
        spec = get_source(BAIDU)
        assert spec.name == BAIDU
        for cap in ("kline", "minute", "tick", "quote"):
            assert cap in spec.capabilities
        assert "fund_flow" not in spec.capabilities

    def test_list_sources_includes_baidu(self):
        assert BAIDU in list_sources(capability="kline")
        assert BAIDU in list_sources(capability="minute")
        assert BAIDU in list_sources(capability="tick")


# --------------------------------------------------------------------------- #
# K 线（口径坑：volume=成交额(元) / amount=成交量(手) → 交换映射）
# --------------------------------------------------------------------------- #
class TestKline:
    def test_volume_amount_swap(self):
        """kline.volume(元)→Bar.amount；kline.amount(手)→Bar.volume×100。"""
        src = _src(_kline_body())
        bars = src.fetch_kline("600519", count=5)
        assert len(bars) == 2
        b = bars[0]
        assert b.datetime == "20260828"
        assert b.open == 1289.0 and b.high == 1297.89 and b.low == 1288.0
        assert b.close == 1297.40
        # amount 字段 16126 手 → 股
        assert b.volume == 1_612_600
        # volume 字段 2086008422.00 → 成交额（元）
        assert b.amount == pytest.approx(2_086_008_422.0)

    def test_ma_extra(self):
        """ma5/ma10/ma20 指标挂到 extra。"""
        bars = _src(_kline_body()).fetch_kline("600519", count=5)
        assert bars[0].extra["ma5"] == pytest.approx(1300.23)
        assert bars[0].extra["ma10"] == pytest.approx(1301.10)
        assert bars[0].extra["ma20"] == pytest.approx(1295.00)
        assert bars[0].extra["turnover_rate"] == pytest.approx(0.13)
        assert bars[0].extra["pct_change"] == pytest.approx(0.09)

    def test_url_params(self):
        """日K URL 带 ktype=1 / count / end_time（unix）。"""
        src = _src(_kline_body())
        src.fetch_kline("600519", period="day", count=10, end_time=1234567890)
        url = src.client.calls[0]
        assert "group=quotation_kline_ab" in url
        assert "ktype=1" in url
        assert "count=10" in url
        assert "end_time=1234567890" in url
        assert "all=0" in url

    def test_week_month_ktype(self):
        for period, kt in (("week", 2), ("month", 3)):
            src = _src(_kline_body())
            src.fetch_kline("600519", period=period)
            url = src.client.calls[0]
            assert f"ktype={kt}" in url

    def test_pagination_collects_pages(self):
        """超过单页上限自动翻页：两页 body 按序轮换，结果全局排序去重。"""
        # 第一页（end_time=now，250 根）返回 D000000..D000249（旧→新）
        page1 = _gen_kline(250, start_ts=1_800_000_000)
        # 第二页（end_time=最旧 time-1，10 根）返回更早的 D000250..D000259
        page2 = _gen_kline(10, start_ts=1_800_000_000 - 260 * 86400)
        src = BaiduSource(max_retries=0)
        src.client = FakeHttp(
            json.dumps(page1).encode("utf-8"),
            json.dumps(page2).encode("utf-8"),
        )
        src.rate_limiter = RateLimiter()
        bars = src.fetch_kline("600519", count=260)
        assert len(src.client.calls) == 2
        assert len(bars) == 260
        dates = [b.datetime for b in bars]
        assert dates == sorted(dates)
        # 旧→新：更早的 page2（D1777536000..）在前，page1（D1800000000..）在后
        assert dates[0] == "D1777536000"
        assert dates[-1] == "D1821513600"
        # 去内部游标键
        assert all("_baidu_time" not in b.extra for b in bars)

    def test_bad_json(self):
        src = BaiduSource(max_retries=0)
        src.client = FakeHttp(b"not-json")
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch_kline("600519", count=5)

    def test_non_ashare_rejected(self):
        with pytest.raises(WebSourceError):
            _src(_kline_body()).fetch_kline("hk00700")


# --------------------------------------------------------------------------- #
# 分时
# --------------------------------------------------------------------------- #
class TestMinute:
    def test_parse_minute(self):
        """volume 手→股；amount 优先 oriAmount（元）。"""
        pts = _src(_minute_body()).fetch_minute("600519")
        assert len(pts) == 2
        assert all(isinstance(p, MinutePoint) for p in pts)
        p0, p1 = pts
        assert p0.time == "09-03 09:30" and p0.price == 1297.50
        assert p0.volume == 10_500  # 105 手 → 股
        assert p0.amount == pytest.approx(13_625_000.0)  # oriAmount（元）
        assert p1.avg_price == pytest.approx(1297.75)
        assert p1.amount == pytest.approx(39_580_000.0)


# --------------------------------------------------------------------------- #
# 五档快照
# --------------------------------------------------------------------------- #
class TestQuote:
    def test_parse_quote(self):
        """快照 + 五档：last_close 由 price-increase 反推；档位手→股。"""
        q = _src(_minute_body()).fetch_quote("600519")
        assert isinstance(q, Quote)
        assert q.code == "600519"
        assert q.price == pytest.approx(1298.88)
        assert q.last_close == pytest.approx(1298.88 - 1.38)
        assert q.volume == 177_476_500  # totalVolume 手 → 股
        assert q.amount == pytest.approx(2_305_193_119.0)
        assert q.extra["name"] == "贵州茅台"
        assert len(q.bid) == 2 and len(q.ask) == 2
        assert q.bid[0].price == pytest.approx(1298.71)
        assert q.bid[0].volume == 500_000  # 5000 手 → 股
        assert q.ask[0].price == pytest.approx(1299.19)


# --------------------------------------------------------------------------- #
# 逐笔
# --------------------------------------------------------------------------- #
class TestTicks:
    def test_parse_ticks(self):
        """bsFlag 映射：S→卖(1) / B→买(0)；volume 手→股。"""
        ticks = _src(_minute_body()).fetch_ticks("600519")
        assert len(ticks) == 3
        assert all(isinstance(t, Tick) for t in ticks)
        assert ticks[0].time == "14:46" and ticks[0].price == 1298.96
        assert ticks[0].volume == 70_000  # 700 手 → 股
        assert ticks[0].buyorsell == 1  # S = 卖
        assert ticks[1].buyorsell == 0  # B = 买
        assert ticks[2].buyorsell == 1


# --------------------------------------------------------------------------- #
# 基类 fetch / parse 出口
# --------------------------------------------------------------------------- #
class TestParseDispatch:
    def test_parse_kline_default(self):
        src = _src(_kline_body())
        bars = src.parse(_kline_body().decode("utf-8"), ["600519"])
        assert bars and isinstance(bars[0], Bar)

    def test_parse_quote_kind(self):
        src = _src(_minute_body())
        quotes = src.parse(_minute_body().decode("utf-8"), ["600519"], kind="quote")
        assert quotes and isinstance(quotes[0], Quote)

    def test_fetch_returns_quotes(self):
        """基类 fetch 走 build_url 默认 kline 组→返回 list[Bar]（降级链按能力自选）。"""
        src = _src(_kline_body())
        out = src.fetch(["600519"])
        assert isinstance(out, list) and out and isinstance(out[0], Bar)
