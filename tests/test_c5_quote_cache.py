"""C5 · QuoteCache TTL 缓存引擎测试 + v13 §5.5 退役契约。

``QuoteCache`` 作为 TTL 存储引擎保留可用；但 ``DataSourceRouter`` 不再持有
quote cache 读穿/回写路径（legacy TTL cache 无 Provider provenance，命中会
改变数据来源）。见 ``docs/REFACTOR_PLAN_v13_CLEAN_BREAK.md`` §5.5。
"""

from __future__ import annotations

import time

import pytest

from tstdx.cache import QuoteCache
from tstdx.sources import DataSourceRouter


class _FakeService:
    """计数服务替身：所有 Provider 请求都落到这里。"""

    def __init__(self) -> None:
        self.calls = 0

    def quotes(self, symbols, *, provider=None, source=None, with_meta=False):  # noqa: ANN001
        self.calls += 1
        return [
            {"code": s, "price": 10.0 + i, "volume": 100, "amount": 1000.0}
            for i, s in enumerate(symbols)
        ]

    def bars(self, symbol, *, period="day", count=320, start=0, adjust="", provider=None):  # noqa: ANN001
        return []

    def close(self) -> None:
        pass


@pytest.fixture()
def router_with_source() -> tuple[DataSourceRouter, _FakeService]:
    fake = _FakeService()
    return DataSourceRouter(service=fake), fake  # type: ignore[arg-type]


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


def test_router_does_not_read_or_write_quote_cache(router_with_source) -> None:
    """v13 §5.5：router 不再读穿/回写 quote cache，每次请求都直达 Provider。"""
    r, fake = router_with_source
    cache = QuoteCache(ttl=5.0)
    r.quote_cache = cache
    try:
        a = r.quotes(["sh600000"])
        b = r.quotes(["sh600000"])

        assert fake.calls == 2  # 没有缓存短路：两次都调用 Provider
        assert a == b
        assert r.last_source == "tdx"
        assert (cache.hits, cache.misses) == (0, 0)  # 缓存引擎完全未被触碰
    finally:
        cache.clear()
        r.close()
