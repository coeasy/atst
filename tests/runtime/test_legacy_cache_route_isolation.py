"""v13 §5.5：legacy cache 与 Provider 路由的隔离契约。

``DataSourceRouter`` 内的 legacy cache 读写路径已退役（存储引擎保留但不再
被隐式使用）。本文件锁定两点：

* 显式 ``order=`` 路由必须绕过（不读不写）注入的 legacy cache；
* 缺省路由同样不再把 legacy cache 当作数据源。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tstdx.sources import DataSourceRouter


class _QuoteCache:
    def __init__(self) -> None:
        self.get_calls = 0
        self.put_calls = 0

    def get(self, symbols):
        self.get_calls += 1
        return [{"code": "600519", "price": 999.0}]

    def put(self, symbols, rows) -> None:
        self.put_calls += 1


class _KlineCache:
    def __init__(self) -> None:
        self.get_calls = 0
        self.merge_calls = 0

    def get(self, symbol, period, count):
        self.get_calls += 1
        return [
            {
                "datetime": "2026-01-01 15:00",
                "open": 999.0,
                "high": 999.0,
                "low": 999.0,
                "close": 999.0,
                "volume": 0,
                "amount": 0.0,
            }
        ] * max(1, count)

    def merge(self, symbol, period, rows) -> None:
        self.merge_calls += 1


class _FakeTdxClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def quotes(self, symbols, *, as_format="dict"):
        return [{"code": "600519", "price": 1.0}]

    def bars(self, symbol, *, period, count, start, as_format):
        # tail 时间戳必须通过 current_series 新鲜度校验，否则断言会落在
        # FreshnessViolation 而不是「缓存未被触碰」上。
        return [
            {
                "datetime": f"{datetime.now().strftime('%Y-%m-%d')} 15:00",
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "amount": 1.0,
            }
        ]


def test_explicit_quote_route_bypasses_unscoped_legacy_cache(monkeypatch) -> None:
    import tstdx.client

    cache = _QuoteCache()
    monkeypatch.setattr(tstdx.client, "TdxClient", _FakeTdxClient)
    router = DataSourceRouter(quote_cache=cache)

    rows = router.quotes(["sh600519"], order=["tdx"])

    assert rows[0]["price"] == 1.0
    assert router.last_source == "tdx"
    assert cache.get_calls == 0
    assert cache.put_calls == 0


def test_explicit_kline_route_bypasses_unscoped_legacy_cache(monkeypatch) -> None:
    import tstdx.client

    cache = _KlineCache()
    monkeypatch.setattr(tstdx.client, "TdxClient", _FakeTdxClient)
    router = DataSourceRouter(kline_cache=cache)

    rows = router.kline("sh600519", period="day", count=1, order=["tdx"])

    assert rows[0]["close"] == 1.0
    assert router.last_source == "tdx"
    assert cache.get_calls == 0
    assert cache.merge_calls == 0


def test_default_route_also_never_consults_legacy_cache(monkeypatch) -> None:
    """缺省路由（确定性 tdx）同样不把 legacy cache 当数据源。"""
    import tstdx.client

    kline_cache = _KlineCache()
    quote_cache = _QuoteCache()
    monkeypatch.setattr(tstdx.client, "TdxClient", _FakeTdxClient)
    router = DataSourceRouter(kline_cache=kline_cache, quote_cache=quote_cache)

    router.kline("sh600519", period="day", count=1)
    router.quotes(["sh600519"])

    assert (kline_cache.get_calls, kline_cache.merge_calls) == (0, 0)
    assert (quote_cache.get_calls, quote_cache.put_calls) == (0, 0)
