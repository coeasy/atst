# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""行情/K 线会话 Mixin 离线单测（覆盖率补强）。

覆盖会话层真正有分支的几处：BOC 源拒绝 ``quotes()``、港美股整市场枚举拒绝、
非新浪/腾讯源的 ``all_market()`` 拒绝、共享连接池的幂等释放、周期别名与
未知周期、分钟 K 线按 provider 换源（含 adjust 签名探测）、history 的
复权/源路由，以及各委托方法的参数透传与 ``finally close()``。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.errors import CompatibilityError, WebSourceError
from atst.sources._session_market import (
    KlineSessionMixin,
    QuoteSessionMixin,
    close_shared_http,
)
from atst.sources.session import WebQuoteSession

pytestmark = pytest.mark.unit


class _Fake:
    """通用假适配器：记录构造参数与调用。"""

    instances: list[_Fake] = []

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.client = client
        self.kwargs = kwargs
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        type(self).instances.append(self)

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return []

        return _call


@pytest.fixture(autouse=True)
def _clean_instances() -> Any:
    _Fake.instances.clear()
    yield
    _Fake.instances.clear()


@pytest.fixture(autouse=True)
def _shared(monkeypatch: pytest.MonkeyPatch) -> Any:
    sentinel = object()
    monkeypatch.setattr("atst.sources._session_market._shared_http", lambda: sentinel)
    return sentinel


def _install(monkeypatch: pytest.MonkeyPatch, target: str, **methods: Any) -> type[_Fake]:
    fake_cls = type("_FakeInstalled", (_Fake,), {})
    for name, handler in methods.items():

        def make(method_name: str, fn: Any) -> Any:
            def method(self: _Fake, *args: Any, **kwargs: Any) -> Any:
                self.calls.append((method_name, args, kwargs))
                if isinstance(fn, Exception):
                    raise fn
                return fn(*args, **kwargs) if callable(fn) else fn

            return method

        setattr(fake_cls, name, make(name, handler))
    monkeypatch.setattr(target, fake_cls)
    return fake_cls


# --------------------------------------------------------------------------- #
# 共享连接池
# --------------------------------------------------------------------------- #
class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_close_shared_http_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Closable()
    monkeypatch.setattr("atst.sources._session_market._SHARED_HTTP", [client])
    assert close_shared_http() is True
    assert client.closed is True
    assert close_shared_http() is False


def test_close_shared_http_without_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atst.sources._session_market._SHARED_HTTP", [])
    assert close_shared_http() is False


def test_shared_http_lazily_builds_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import atst.sources._session_market as mod

    built = _Closable()
    monkeypatch.setattr(mod, "_SHARED_HTTP", [])
    monkeypatch.setattr("atst.sources.base.build_client", lambda **kwargs: built)
    assert mod.shared_http() is built
    assert mod.shared_http() is built
    assert [built] == mod._SHARED_HTTP


def test_capability_adapter_routes_declared_factory() -> None:
    from atst.sources._session_market import _capability_adapter

    cls = _capability_adapter("history", provider="sina")
    assert cls.__name__ == "SinaHistoryKlineSource"


def test_index_quotes_cover_index_symbols() -> None:
    class Fake(_Fake):
        def fetch(self, symbols: list[str]) -> list[str]:
            self.calls.append(("fetch", (symbols,), {}))
            return ["idx"]

    fake = Fake()
    session = WebQuoteSession("sina")
    session._client = fake
    assert session.index() == ["idx"]
    assert set(fake.calls[0][1][0]) == {"sh000001", "sz399001", "sz399006", "sh000300"}


def test_hk_us_quotes_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "atst.sources.tencent.adapters.HkSource", fetch=["hk-tencent"])
    _install(monkeypatch, "atst.sources.sina.adapters.SinaHkSource", fetch=["hk-sina"])
    _install(monkeypatch, "atst.sources.tencent.adapters.UsSource", fetch=["us"])

    assert QuoteSessionMixin.hk_quotes(["00700"]) == ["hk-tencent"]
    assert QuoteSessionMixin.hk_quotes("00700", provider="sina") == ["hk-sina"]
    assert QuoteSessionMixin.us_quotes("AAPL") == ["us"]

    hk_tencent, hk_sina, us = _Fake.instances
    assert hk_tencent.calls == [("fetch", (["00700"],), {})]
    assert hk_sina.calls == [("fetch", (["00700"],), {})]
    assert us.calls == [("fetch", (["AAPL"],), {})]
    assert all(inst.closed for inst in (hk_tencent, hk_sina, us))


# --------------------------------------------------------------------------- #
# quotes / all_market
# --------------------------------------------------------------------------- #
def test_quotes_boc_source_is_rejected() -> None:
    session = WebQuoteSession("boc")
    with pytest.raises(CompatibilityError) as caught:
        session.quotes(["600519"])
    assert caught.value.code == "E8000"
    assert "rates()" in str(caught.value)


def test_quotes_normalizes_symbols_and_wraps_os_error() -> None:
    class Ok(_Fake):
        def fetch(self, symbols: list[str]) -> list[str]:
            self.calls.append(("fetch", (symbols,), {}))
            return ["q"]

    ok = Ok()
    session = WebQuoteSession("sina")
    session._client = ok
    assert session.quotes("600519") == ["q"]
    assert ok.calls == [("fetch", (["sh600519"],), {})]

    class Down(_Fake):
        def fetch(self, symbols: list[str]) -> list[str]:
            raise ConnectionError("network down")

    session._client = Down()
    with pytest.raises(CompatibilityError, match="network down") as caught:
        session.quotes(["600519"])
    assert isinstance(caught.value.cause, ConnectionError)


@pytest.mark.parametrize("node", ["hk", "us", "hk_main", "us_main", "HK"])
def test_all_market_rejects_hk_us(node: str) -> None:
    with pytest.raises(ValueError, match="港股/美股"):
        WebQuoteSession("sina").all_market(node=node)


def test_all_market_rejects_unsupported_source() -> None:
    with pytest.raises(CompatibilityError, match="仅新浪/腾讯源支持") as caught:
        WebQuoteSession("boc").all_market()
    assert caught.value.code == "E8000"


def test_all_market_delegates_paging() -> None:
    class Fake(_Fake):
        def fetch_all(self, *, node: str, page_size: int, max_pages: int | None) -> list[str]:
            self.calls.append(
                ("fetch_all", (), {"node": node, "page_size": page_size, "max_pages": max_pages})
            )
            return ["row"]

    fake = Fake()
    session = WebQuoteSession("tencent")
    session._client = fake
    assert session.all_market(node="cyb", page_size=50, max_pages=2) == ["row"]
    assert fake.calls == [("fetch_all", (), {"node": "cyb", "page_size": 50, "max_pages": 2})]


# --------------------------------------------------------------------------- #
# globals / market_stat / rates
# --------------------------------------------------------------------------- #
def test_globals_and_market_stat(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "atst.sources.global_market.TencentGlobalSource", fetch=["CL"])
    _install(monkeypatch, "atst.sources.global_market.TencentMarketStatSource", fetch=["stat"])

    assert QuoteSessionMixin.globals() == ["CL"]
    assert QuoteSessionMixin.globals(["CL", "GC"]) == ["CL"]
    assert QuoteSessionMixin.market_stat(None) == ["stat"]
    assert QuoteSessionMixin.market_stat(["sh000001"]) == ["stat"]

    global_inst, stat_inst = _Fake.instances[0], _Fake.instances[2]
    assert global_inst.calls[0] == ("fetch", ([],), {})
    assert stat_inst.calls[0] == ("fetch", ([],), {})


def test_rates_forwards_session_kwargs(monkeypatch: pytest.MonkeyPatch, _shared: Any) -> None:
    _install(monkeypatch, "atst.sources.boc.adapters.BocSource", fetch_rates=[{"currency": "美元"}])
    session = WebQuoteSession("boc", cookie="canned")
    assert session.rates() == [{"currency": "美元"}]
    inst = _Fake.instances[0]
    assert inst.kwargs["cookie"] == "canned"
    assert inst.client is _shared
    assert inst.closed is True


# --------------------------------------------------------------------------- #
# 符号归一化回退
# --------------------------------------------------------------------------- #
def test_normalize_falls_back_to_lower_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_code: str) -> str:
        raise ValueError("bad symbol")

    monkeypatch.setattr("atst.sources._session_market.normalize_symbol", boom)
    assert QuoteSessionMixin._normalize(" Sh600519 ") == "sh600519"


# --------------------------------------------------------------------------- #
# K 线
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("period", ["year", "tick", "season"])
def test_klines_rejects_unknown_period(period: str) -> None:
    with pytest.raises(ValueError, match="未知 K 线周期"):
        KlineSessionMixin().klines("600519", period=period)


def test_klines_day_path(monkeypatch: pytest.MonkeyPatch, _shared: Any) -> None:
    _install(monkeypatch, "atst.sources.tencent.adapters.KlineSource", fetch_bars=["bar"])
    out = KlineSessionMixin().klines("600519", period="d", count=10, adjust="hfq")
    assert out == ["bar"]
    inst = _Fake.instances[0]
    assert inst.client is _shared
    assert inst.calls == [
        ("fetch_bars", ("600519",), {"period": "day", "count": 10, "adjust": "hfq"})
    ]
    assert inst.closed is True


class _MinuteWithAdjust(_Fake):
    def fetch_bars(
        self, symbol: str, *, period: str, count: int, adjust: str = ""
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("fetch_bars", (symbol,), {"period": period, "count": count, "adjust": adjust})
        )
        return [{"symbol": symbol}]


class _MinuteNoAdjust(_Fake):
    def fetch_bars(self, symbol: str, *, period: str, count: int) -> list[dict[str, Any]]:
        self.calls.append(("fetch_bars", (symbol,), {"period": period, "count": count}))
        return [{"symbol": symbol}]


def _patch_capability(
    monkeypatch: pytest.MonkeyPatch, fake_cls: type[_Fake]
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake(
        capability: str, *, provider: str | None = None, symbol: str | None = None
    ) -> type[_Fake]:
        calls.append((capability, provider, symbol))
        return fake_cls

    monkeypatch.setattr("atst.sources._session_market._capability_adapter", fake)
    return calls


def test_klines_minute_routing_passes_adjust(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_capability(monkeypatch, _MinuteWithAdjust)
    out = KlineSessionMixin().klines("hk00700", period="5m", count=60)
    assert out == [{"symbol": "hk00700"}]
    assert calls == [("minute_klines", None, "hk00700")]
    inst = _Fake.instances[0]
    assert inst.calls == [
        ("fetch_bars", ("hk00700",), {"period": "5min", "count": 60, "adjust": "qfq"})
    ]
    assert inst.closed is True


def test_klines_minute_routing_omits_adjust_when_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_capability(monkeypatch, _MinuteNoAdjust)
    KlineSessionMixin().klines("600519", period="m1", count=5)
    inst = _Fake.instances[0]
    assert inst.calls == [("fetch_bars", ("600519",), {"period": "1min", "count": 5})]
    assert "adjust" not in inst.calls[0][2]


def test_klines_upstream_error_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.tencent.adapters.KlineSource",
        fetch_bars=WebSourceError("kline upstream down", context={"source": "tencent"}),
    )
    with pytest.raises(WebSourceError, match="upstream down"):
        KlineSessionMixin().klines("600519")
    assert _Fake.instances[0].closed is True


# --------------------------------------------------------------------------- #
# 分时 / 逐笔 / 历史
# --------------------------------------------------------------------------- #
def test_minute_ticks_intraday_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "atst.sources.tencent.adapters.MinuteSource", fetch_minute=["m"])
    _install(monkeypatch, "atst.sources.ticks.TencentTickSource", fetch_ticks=["t"])
    _install(monkeypatch, "atst.sources.ticks.EastmoneyTrendsSource", fetch_minutes=["i"])

    assert KlineSessionMixin().minute("600519") == ["m"]
    assert KlineSessionMixin.ticks("600519", max_pages=2) == ["t"]
    assert KlineSessionMixin.intraday("600519") == ["i"]

    minute, tick, trend = _Fake.instances
    assert minute.calls == [("fetch_minute", ("600519",), {})]
    assert tick.calls == [("fetch_ticks", ("600519",), {"max_pages": 2})]
    assert trend.calls == [("fetch_minutes", ("600519",), {})]
    assert all(inst.closed for inst in (minute, tick, trend))


class _HistoryFake(_Fake):
    def fetch_bars(
        self, symbol: str, *, period: str, count: int, adjust: str
    ) -> list[dict[str, Any]]:
        self.calls.append(
            ("fetch_bars", (symbol,), {"period": period, "count": count, "adjust": adjust})
        )
        return [{"symbol": symbol}]


@pytest.mark.parametrize(
    ("kwargs", "provider"),
    [
        ({"adjust": ""}, "sina"),
        ({"adjust": "qfq"}, "eastmoney"),
        ({"adjust": "hfq"}, "eastmoney"),
        ({"source": "eastmoney", "adjust": ""}, "eastmoney"),
    ],
)
def test_history_provider_routing(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], provider: str
) -> None:
    calls = _patch_capability(monkeypatch, _HistoryFake)
    out = KlineSessionMixin.history("600519", period="d", count=9, **kwargs)
    assert out == [{"symbol": "600519"}]
    assert calls == [("history", provider, None)]
    inst = _Fake.instances[0]
    assert inst.calls == [
        ("fetch_bars", ("600519",), {"period": "day", "count": 9, "adjust": kwargs["adjust"]})
    ]
    assert inst.closed is True
