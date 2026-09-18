"""efinance 对标门面端到端集成测试（离线，罐头客户端）。

通过 monkeypatch ``_shared_http`` 注入假客户端，验证
``WebQuoteSession`` → ``WebQuoteSession`` Mixin → 各 Web 源 的完整调用链
（覆盖 FundMobSource / EastmoneyFuturesSource / EastmoneyBondSource）。
不发起任何真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

import tstdx.web._session_efinance as mixin_mod
from tstdx.web.base import HttpResponse
from tstdx.web.session import WebQuoteSession


class FakeHttpClient:
    """按 URL 关键字返回预置响应的假客户端。"""

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


BASE_INFO = _j(
    {
        "Datas": {
            "FCODE": "161725",
            "SHORTNAME": "招商中证白酒指数(LOF)A",
            "ESTABDATE": "2015-05-27",
            "RZDF": "-6.03",
            "DWJZ": "1.1959",
            "JJGS": "招商基金",
            "FSRQ": "2026-07-30",
        }
    }
)
HOLDINGS = _j(
    {
        "Datas": {
            "fundStocks": [
                {"GPDM": "600519", "GPJC": "贵州茅台", "JZBL": "16.78", "PCTNVCHG": "1.36"}
            ]
        },
        "Expansion": "2022-03-31",
    }
)
PERIOD_CHANGE = _j(
    {"Datas": [{"title": "近1周", "syl": "-2.31", "avg": "-1.12", "rank": "120", "sc": "1500"}]}
)
ASSET_ALLOC = _j(
    {
        "Datas": [
            {
                "FSRQ": "2022-03-31",
                "GP": "85.3",
                "ZQ": "3.2",
                "HB": "5.1",
                "QT": "6.4",
                "JZC": "620.5",
            }
        ]
    }
)
INDUSTRY = _j({"Datas": [{"HYMC": "制造业", "ZJZBL": "45.2", "FSRQ": "2022-03-31", "SZ": "280.1"}]})
PUBLIC_DATES = _j({"Datas": ["2021-03-31", "2021-06-30"]})
MANAGER_HTML = (
    "<html><body><div class='bs_gl'><label>基金经理</label>"
    "<a href='#'>王平</a></div><span>2022-03-31</span></body></html>"
).encode()

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
KLINES = _j({"data": {"klines": ["2021-08-17,755.0,770.8,776.0,750.6,82373,6288335376.0"]}})

# 全市场可转债列表（push2 clist，fs=m:128,m:80）
BOND_CLIST = _j(
    {
        "data": {
            "diff": [
                {
                    "f12": "113050",
                    "f13": "1",
                    "f14": "嘉澳转债",
                    "f3": 125,
                    "f104": 10000,
                    "f106": 10234,
                },
                {
                    "f12": "127068",
                    "f13": "0",
                    "f14": "南银转债",
                    "f3": -32,
                    "f104": 11000,
                    "f106": 10648,
                },
            ]
        }
    }
)


@pytest.fixture
def api(monkeypatch):
    """注入假客户端的 WebQuoteSession。"""

    created: list[FakeHttpClient] = []

    def _factory(**bodies: bytes) -> FakeHttpClient:
        fc = FakeHttpClient(**bodies)
        created.append(fc)
        return fc

    def _shared() -> FakeHttpClient:
        # 每个源都会调一次 _shared_http()，返回同一个带全部罐头的客户端
        if not created:
            created.append(FakeHttpClient(**_ALL))
        return created[0]

    monkeypatch.setattr(mixin_mod, "_shared_http", _shared)
    return WebQuoteSession(), created


_ALL = {
    "FundMNNBasicInformation": BASE_INFO,
    "FundMNInverstPosition": HOLDINGS,
    "FundMNPeriodIncrease": PERIOD_CHANGE,
    "FundMNAssetAllocationNew": ASSET_ALLOC,
    "FundMNSectorAllocation": INDUSTRY,
    "FundMNIVInfoMultiple": PUBLIC_DATES,
    "jjjl_161725.html": MANAGER_HTML,
    "stock/get": SNAPSHOT,
    "stock/kline/get": KLINES,
    "m:128": BOND_CLIST,
}


class TestFundFacade:
    def test_base_info(self, api) -> None:
        api_obj, _ = api
        info = api_obj.fund_base_info("161725")
        assert info["code"] == "161725"
        assert info["unit_nav"] == pytest.approx(1.1959)

    def test_base_info_multi(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.fund_base_info_multi(["161725", "161726"])
        assert len(rows) == 2
        assert all(r["unit_nav"] == pytest.approx(1.1959) for r in rows)

    def test_manager(self, api) -> None:
        api_obj, _ = api
        mgr = api_obj.fund_manager("161725")
        assert mgr is not None
        assert "王平" in mgr["manager"]

    def test_holdings(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.fund_holdings("161725")
        assert rows[0]["code"] == "600519"

    def test_period_change(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.fund_period_change("161725")
        assert rows[0]["return_rate"] == pytest.approx(-2.31)

    def test_asset_allocation(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.fund_asset_allocation("161725")
        assert rows[0]["stock"] == pytest.approx(85.3)

    def test_industry_distribution(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.fund_industry_distribution("161725")
        assert rows[0]["industry"] == "制造业"

    def test_public_dates(self, api) -> None:
        api_obj, _ = api
        dates = api_obj.fund_public_dates("161725")
        assert dates == ["2021-03-31", "2021-06-30"]


class TestDerivativeFacade:
    def test_futures_realtime(self, api) -> None:
        api_obj, _ = api
        q = api_obj.futures_realtime("115.ZCM")
        assert q["price"] == pytest.approx(836.60)

    def test_futures_kline(self, api) -> None:
        api_obj, _ = api
        bars = api_obj.futures_kline("115.ZCM", period="day", count=1)
        assert bars[0].close == pytest.approx(770.8)

    def test_bond_realtime(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.bond_realtime(["113050"])
        assert rows[0]["price"] == pytest.approx(836.60)

    def test_bond_kline(self, api) -> None:
        api_obj, _ = api
        bars = api_obj.bond_kline("113050", period="day", count=1)
        assert bars[0].close == pytest.approx(770.8)

    def test_bond_all_base_info(self, api) -> None:
        api_obj, _ = api
        rows = api_obj.bond_all_base_info()
        assert len(rows) == 2
        assert rows[0]["code"] == "113050"
        assert rows[0]["name"] == "嘉澳转债"
        assert rows[0]["price"] == pytest.approx(102.34)
        assert rows[1]["change_pct"] == pytest.approx(-0.32)
