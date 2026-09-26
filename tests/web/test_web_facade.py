"""Web 高层门面测试（§33）。

覆盖：源别名、符号归一、字符串输入、全市场 all_market 分页、
全市场行解析、非新浪源 all_market 提示、K 线周期别名、指数代码表。
全部离线，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import CompatibilityError
from tstdx.web.base import HttpResponse, RateLimiter
from tstdx.web.session import (
    INDEX_SYMBOLS,
    KLINES_PERIOD_ALIASES,
    SOURCE_ALIASES,
    WebQuoteSession,
    web_session,
)
from tstdx.web.sina.adapters import SinaSource
from tstdx.web.sources import TENCENT
from tstdx.web.tencent.adapters import TencentSource

# --------------------------------------------------------------------------- #
# 罐头数据
# --------------------------------------------------------------------------- #
MARKET_ROW = {
    "symbol": "sh600519",
    "code": "600519",
    "name": "贵州茅台",
    "trade": 1292.10,
    "settlement": 1286.00,
    "open": 1292.10,
    "high": 1305.00,
    "low": 1286.00,
    "volume": 1520800,
    "amount": 7946130000.0,
    "buy": 1292.00,
    "sell": 1291.00,
    "changepercent": 0.47,
    "pricechange": 6.10,
    "per": 45.2,
    "pb": 8.9,
    "mktcap": 16230000000000.0,
    "nmc": 16230000000000.0,
    "turnoverratio": 0.12,
    "ticktime": "2026-08-31 15:00:00",
}


class FakeHttp:
    """返回预置分页 JSON 的假 HTTP 客户端。"""

    def __init__(self, pages: list[list[dict]]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        idx = min(len(self.calls) - 1, len(self.pages) - 1)
        return HttpResponse(200, json.dumps(self.pages[idx]).encode("gbk"), {})

    def close(self):
        pass


class _FakeFetch:
    """session.quotes() 底层的假 fetch。"""

    def __init__(self, symbols):
        self.symbols = symbols

    def fetch(self, symbols, **kwargs):  # noqa: ARG002
        return [
            Quote(
                code=s,
                price=1.0,
                last_close=1.0,
                open=1.0,
                high=1.0,
                low=1.0,
                volume=0,
                amount=0.0,
                extra={"name": "测试"},
            )
            for s in symbols
        ]

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 门面纯逻辑
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestFacadeMapping:
    def test_source_aliases(self):
        """源别名映射：qq→tencent、em→eastmoney 等。"""
        assert SOURCE_ALIASES["qq"] == TENCENT
        assert SOURCE_ALIASES["em"] == "eastmoney"
        assert SOURCE_ALIASES["sina"] == "sina"

    def test_klines_period_aliases(self):
        """K 线周期别名映射。"""
        assert KLINES_PERIOD_ALIASES["day"] == "day"
        assert KLINES_PERIOD_ALIASES["m1"] == "1min"
        assert KLINES_PERIOD_ALIASES["m5"] == "5min"
        assert KLINES_PERIOD_ALIASES["m60"] == "60min"
        assert KLINES_PERIOD_ALIASES["week"] == "week"

    def test_index_symbols(self):
        """指数代码表。"""
        assert INDEX_SYMBOLS["上证指数"] == "sh000001"
        assert INDEX_SYMBOLS["创业板指"] == "sz399006"

    def test_unknown_source(self):
        """未知源名 → CompatibilityError。"""
        with pytest.raises(CompatibilityError):
            web_session("nonexistent")

    def test_normalize_market_inference(self):
        """会话符号市场推断（委托统一符号引擎，与 symbol.py 约定一致）。"""
        s = WebQuoteSession("sina")
        assert s._normalize("600519") == "sh600519"  # 6 开头 → 沪
        # 000001：裸码归深市（平安银行）——symbol.py 裁决：需要上证指数时
        # 显式书写 sh000001（仅白名单裸码 000300/000905 等默认归沪）
        assert s._normalize("000001") == "sz000001"
        assert s._normalize("sh000001") == "sh000001"
        assert s._normalize("sh600519") == "sh600519"


# --------------------------------------------------------------------------- #
# 全市场
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestMarketAll:
    def _source(self, pages):
        src = SinaSource(max_retries=0)
        src.client = FakeHttp(pages)
        src.rate_limiter = RateLimiter()
        return src

    def test_quote_from_market_row(self):
        """全市场单行解析。"""
        src = SinaSource(max_retries=0)
        q = src._quote_from_market_row(MARKET_ROW)
        assert isinstance(q, Quote)
        assert q.code == "sh600519"
        assert q.price == 1292.10
        assert q.volume == 1520800
        assert q.amount == 7946130000.0
        assert q.bid[0].price == 1292.00
        assert q.extra["name"] == "贵州茅台"
        assert q.extra["time"] == "15:00:00"

    def test_fetch_all_pagination(self):
        """分页：满页继续、不足页终止（M3 并发下数据完整、调用≥2）。"""
        pages = [
            [MARKET_ROW, {"symbol": "sz000001", "name": "平安银行"}],
            [{"symbol": "sh600000", "name": "浦发银行"}],
        ]
        src = self._source(pages)
        quotes = src.fetch_all(page_size=2)
        assert len(quotes) == 3
        # 并发分页：第 1 页 + 余数页至少各 1 次；可能有并发在飞请求命中同一
        # 短页（无副作用），故不精确断言调用数
        assert len(src.client.calls) >= 2

    def test_fetch_all_max_pages(self):
        """max_pages 截断。"""
        pages = [[MARKET_ROW] for _ in range(5)]
        src = self._source(pages)
        quotes = src.fetch_all(page_size=1, max_pages=2)
        assert len(quotes) == 2
        assert len(src.client.calls) == 2

    def test_session_all_market_sina(self):
        """门面 all_market()：新浪源返回 list[Quote]。"""
        pages = [[MARKET_ROW]]
        src = SinaSource(max_retries=0)
        src.client = FakeHttp(pages)
        src.rate_limiter = RateLimiter()
        sess = WebQuoteSession("sina")
        sess._client = src  # 注入假客户端
        out = sess.all_market(page_size=2)
        assert len(out) == 1
        assert out[0].code == "sh600519"
        assert out[0].price == 1292.10

    def test_session_all_market_tencent(self, monkeypatch):
        """门面 all_market()：腾讯源走 TencentSource.fetch_all（U5）。"""
        sentinel = [
            Quote(
                code="sh600519",
                price=1.0,
                last_close=1.0,
                open=1.0,
                high=1.0,
                low=1.0,
                volume=0,
                amount=0.0,
                extra={"name": "测试"},
            )
        ]
        received: dict = {}

        def fake_fetch_all(self, **kwargs):
            received.update(kwargs)
            return sentinel

        monkeypatch.setattr(TencentSource, "fetch_all", fake_fetch_all)
        sess = web_session("tencent")
        out = sess.all_market(node="hs_a", page_size=200)
        assert out == sentinel
        assert received == {"node": "hs_a", "page_size": 200, "max_pages": None}

    def test_session_all_market_unsupported_source(self):
        """东财等非新浪/腾讯源 all_market() → CompatibilityError（含替代提示）。"""
        sess = WebQuoteSession("eastmoney")
        with pytest.raises(CompatibilityError, match="tencent"):
            sess.all_market()

    def test_session_quotes_string_input(self):
        """quotes 传单个字符串 → 归一为列表。"""
        sess = WebQuoteSession("sina")
        sess._client = _FakeFetch(["sh600519"])
        out = sess.quotes("600519")  # type: ignore[arg-type]
        assert len(out) == 1
        assert out[0].code == "sh600519"

    def test_session_index(self):
        """index() 走原生 Quote，不携带反直觉字段。"""
        sess = WebQuoteSession("sina")
        sess._client = _FakeFetch(list(INDEX_SYMBOLS.values()))
        out = sess.index()
        assert len(out) == len(INDEX_SYMBOLS)
        # 原生契约：volume 就是成交量（股），不存在 turnover/volume 互换
        assert all(isinstance(q, Quote) for q in out)


class TestSessionContextManager:
    """P12：WebQuoteSession 支持 with 语法（__enter__/__exit__ 关闭底层连接）。"""

    def test_with_block_closes_session(self):
        from tstdx.web.session import WebQuoteSession

        closed: list[bool] = []
        sess = WebQuoteSession("sina")
        orig_close = sess.close
        sess.close = lambda: (closed.append(True), orig_close())  # type: ignore[assignment]
        with sess as s:
            assert s is sess
        assert closed == [True]

    def test_exit_closes_even_on_error(self):
        from tstdx.web.session import WebQuoteSession

        closed: list[bool] = []
        sess = WebQuoteSession("sina")
        orig_close = sess.close
        sess.close = lambda: (closed.append(True), orig_close())  # type: ignore[assignment]
        with pytest.raises(RuntimeError), sess:
            raise RuntimeError("boom")
        assert closed == [True]
