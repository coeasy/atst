"""C5 · QuoteCache TTL 缓存与 router 读穿接线测试。"""

from __future__ import annotations

import time

import pytest

from tstdx.cache import QuoteCache
from tstdx.sources import DataSourceRouter


class _FakeQuoteSource:
    """计数 fake：任何 tdx/web 源命中都走这里。"""

    def __init__(self) -> None:
        self.calls = 0

    def quotes(self, symbols, *, as_format: str = "dict"):  # noqa: ARG002
        self.calls += 1
        return [
            {"code": s, "price": 10.0 + i, "volume": 100, "amount": 1000.0}
            for i, s in enumerate(symbols)
        ]


@pytest.fixture()
def router_with_source(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeQuoteSource()

    class _FakeWebClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def quotes(self, symbols):
            return fake.quotes(symbols)

        def close(self) -> None:
            pass

    import tstdx.sources as src_mod

    monkeypatch.setattr(src_mod, "_active_sources_for_test", None, raising=False)
    r = DataSourceRouter(order=["web"])
    # monkeypatch WebQuoteClient 引用（函数内 from ..web import 延迟导入）
    import tstdx.web as web_mod

    monkeypatch.setattr(web_mod, "WebQuoteClient", _FakeWebClient, raising=False)
    return r, fake


def test_quote_cache_ttl_hit_and_miss() -> None:
    c = QuoteCache(ttl=0.05)
    rows = [{"code": "sh600000", "price": 1.0}]
    assert c.get(["sh600000"]) is None
    c.put(["sh600000"], rows)
    got = c.get(["sh600000"])
    assert got is not None and got[0]["code"] == "sh600000"
    # 返回拷贝：改写不影响缓存
    got[0]["price"] = 999.0
    assert c.get(["sh600000"])[0]["price"] == 1.0
    # TTL 过期
    time.sleep(0.06)
    assert c.get(["sh600000"]) is None
    assert (c.hits, c.misses) == (2, 2)


def test_router_quotes_readthrough(router_with_source) -> None:
    r, fake = router_with_source
    cache = QuoteCache(ttl=5.0)
    r.quote_cache = cache
    a = r.quotes(["sh600000"])
    b = r.quotes(["sh600000"])
    assert fake.calls == 1  # 第二次命中缓存，源不再被调用
    assert a == b
    assert r.last_source == "quote_cache"
    cache.clear()
    r.quotes(["sh600000"])
    assert fake.calls == 2
