"""期货 / 债券 Web 源（efinance.futures / efinance.bond 对标）离线测试。

全部用罐头 JSON，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from tstdx.web.base import HttpResponse
from tstdx.web.efinance_deriv import EastmoneyBondSource, EastmoneyFuturesSource

_KLINES = ["2021-08-17,755.0,770.8,776.0,750.6,82373,6288335376.0"]


class FakeHttpClient:
    def __init__(self, **bodies: bytes):
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url, *, headers=None, timeout=5.0):
        self.calls.append(url)
        for key, body in self.bodies.items():
            if key in url:
                return HttpResponse(200, body, {})
        return HttpResponse(404, b"not found", {})

    def close(self):
        pass


def _j(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")


CLIST = _j(
    {
        "data": {
            "diff": [
                {
                    "f12": "ZCM",
                    "f13": 115,
                    "f14": "动力煤主力",
                    "f3": 6.28,
                    "f104": 793.0,
                    "f105": 796.8,
                    "f106": 836.6,
                    "f128": "郑商所",
                }
            ]
        }
    }
)

SNAPSHOT = _j(
    {
        "data": {
            "f12": "ZCM",
            "f14": "动力煤主力",
            "f43": 83660,
            "f44": 79300,
            "f45": 79680,
            "f46": 84380,
            "f47": 79680,
            "f48": 82954,
            "f170": 628,
        }
    }
)

KLINES = _j({"data": {"klines": _KLINES}})

DETAILS = _j({"data": {"details": ["21:00:00,879.0,23,0", "21:00:01,878.0,0,-373"]}})


def _futures(bodies: dict[str, bytes]) -> EastmoneyFuturesSource:
    return EastmoneyFuturesSource(client=FakeHttpClient(**bodies))


def _bond(bodies: dict[str, bytes]) -> EastmoneyBondSource:
    return EastmoneyBondSource(client=FakeHttpClient(**bodies))


class TestKlineParse:
    def test_parse_klines(self) -> None:
        bars = EastmoneyFuturesSource._parse_klines({"data": {"klines": _KLINES}})
        assert len(bars) == 1
        b = bars[0]
        assert b.datetime == "2021-08-17"
        assert b.open == pytest.approx(755.0)
        assert b.close == pytest.approx(770.8)
        assert b.high == pytest.approx(776.0)
        assert b.low == pytest.approx(750.6)
        assert b.volume == 82373
        assert b.amount == pytest.approx(6288335376.0)

    def test_parse_klines_empty(self) -> None:
        assert EastmoneyFuturesSource._parse_klines({"data": {}}) == []


class TestFuturesSource:
    def test_base_info(self) -> None:
        src = _futures({"clist/get": CLIST})
        rows = src.fetch_base_info()
        assert len(rows) == 1
        assert rows[0]["quote_id"] == "115.ZCM"
        assert rows[0]["name"] == "动力煤主力"
        assert rows[0]["change_pct"] == pytest.approx(6.28)

    def test_realtime(self) -> None:
        src = _futures({"stock/get": SNAPSHOT})
        q = src.fetch_realtime("115.ZCM")
        assert q["quote_id"] == "115.ZCM"
        assert q["price"] == pytest.approx(836.60)  # 原值 /100
        assert q["change_pct"] == pytest.approx(6.28)  # 原值 /100

    def test_kline(self) -> None:
        src = _futures({"stock/kline/get": KLINES})
        bars = src.fetch_kline("115.ZCM", period="day", count=1)
        assert len(bars) == 1
        assert bars[0].close == pytest.approx(770.8)

    def test_deal_detail(self) -> None:
        src = _futures({"drgtx/get": DETAILS})
        rows = src.fetch_deal_detail("115.ZCM")
        assert len(rows) == 2
        assert rows[0]["price"] == pytest.approx(879.0)
        assert rows[0]["volume"] == 23


class TestBondSource:
    def test_realtime(self) -> None:
        src = _bond({"stock/get": SNAPSHOT})
        rows = src.fetch_realtime(["113050"])
        assert len(rows) == 1
        assert rows[0]["code"] == "ZCM"  # 来自罐头快照 f12
        assert rows[0]["price"] == pytest.approx(836.60)

    def test_kline(self) -> None:
        src = _bond({"stock/kline/get": KLINES})
        bars = src.fetch_kline("113050", period="day", count=1)
        assert len(bars) == 1
        assert bars[0].close == pytest.approx(770.8)

    def test_secid_mapping(self) -> None:
        src = _bond({})
        assert src._secid("113050") == "1.113050"
        assert src._secid("sh600519") == "1.600519"
