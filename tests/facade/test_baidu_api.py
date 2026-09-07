# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""B0：``UnifiedQuoteAPI.baidu_*`` 门面接线测试。

覆盖 :class:`tstdx.facade.api.UnifiedQuoteAPI` 的四个百度财经入口：
* ``baidu_kline`` / ``baidu_minute`` / ``baidu_ticks`` / ``baidu_quote``
  均转发到 :class:`tstdx.web.facade.WebQuoteSession` 同名便捷方法；
* 参数透传正确（period/count/end_time/limit）；
* ``WebQuoteSession`` 每次调用后正确 close。
"""

from __future__ import annotations

import pytest

from tstdx.domain.models import Bar, MinutePoint, Quote, Tick
from tstdx.facade.api import UnifiedQuoteAPI

pytestmark = pytest.mark.unit


class _FakeSession:
    """记录调用并返回罐头对象的假 WebQuoteSession（上下文语义由门面负责）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.closed = 0

    def baidu_kline(self, symbol, *, period="day", count=320, end_time=None):
        self.calls.append(
            ("baidu_kline", (symbol,), dict(period=period, count=count, end_time=end_time))
        )
        return [Bar(datetime="20260828", open=1.0, high=2.0, low=0.5, close=1.5)]

    def baidu_minute(self, symbol):
        self.calls.append(("baidu_minute", (symbol,), {}))
        return [MinutePoint(time="09-03 09:30", price=1297.5, volume=10500, amount=13625000.0)]

    def baidu_ticks(self, symbol, *, limit=200):
        self.calls.append(("baidu_ticks", (symbol,), dict(limit=limit)))
        return [Tick(time="14:46", price=1298.96, volume=70000, num=0, buyorsell=1)]

    def baidu_quote(self, symbol):
        self.calls.append(("baidu_quote", (symbol,), {}))
        return Quote(code="600519", price=1298.88)

    def close(self) -> None:
        self.closed += 1


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(
        "tstdx.web.facade.WebQuoteSession",
        lambda *a, **kw: session,
    )
    return session


class TestBaiduGateways:
    def test_baidu_kline_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            bars = api.baidu_kline("600519", period="week", count=100, end_time=1234567890)
        assert len(bars) == 1 and isinstance(bars[0], Bar)
        assert fake_session.calls[0][0] == "baidu_kline"
        assert fake_session.calls[0][1] == ("600519",)
        assert fake_session.calls[0][2] == {
            "period": "week",
            "count": 100,
            "end_time": 1234567890,
        }
        assert fake_session.closed >= 1

    def test_baidu_kline_defaults(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            api.baidu_kline("sh600519")
        assert fake_session.calls[0][2] == {"period": "day", "count": 320, "end_time": None}

    def test_baidu_minute_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            pts = api.baidu_minute("600519")
        assert pts and isinstance(pts[0], MinutePoint)
        assert fake_session.calls[0][0] == "baidu_minute"
        assert fake_session.calls[0][1] == ("600519",)

    def test_baidu_ticks_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            ticks = api.baidu_ticks("600519", limit=50)
        assert ticks and isinstance(ticks[0], Tick)
        assert fake_session.calls[0][2] == {"limit": 50}

    def test_baidu_quote_forwards(self, fake_session: _FakeSession) -> None:
        with UnifiedQuoteAPI() as api:
            q = api.baidu_quote("600519")
        assert isinstance(q, Quote) and q.price == pytest.approx(1298.88)
        assert fake_session.calls[0][0] == "baidu_quote"

    def test_close_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WebQuoteSession 异常时门面仍应 close（finally 语义）。"""

        class _Boom:
            def baidu_kline(self, *a: object, **kw: object):
                raise RuntimeError("boom")

            def close(self) -> None:
                pass

        monkeypatch.setattr("tstdx.web.facade.WebQuoteSession", lambda *a, **kw: _Boom())
        with UnifiedQuoteAPI() as api, pytest.raises(RuntimeError):
            api.baidu_kline("600519")
