"""东财基金移动端源（efinance.fund 对标）离线测试。

全部用罐头 JSON / HTML，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from atst.web.base import HttpResponse
from atst.web.efinance_fund import FundMobSource


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
            "COMMENTS": "产品特色：布局白酒领域。",
        }
    }
)

HOLDINGS = _j(
    {
        "Datas": {
            "fundStocks": [
                {"GPDM": "600519", "GPJC": "贵州茅台", "JZBL": "16.78", "PCTNVCHG": "1.36"},
                {"GPDM": "600809", "GPJC": "山西汾酒", "JZBL": "15.20", "PCTNVCHG": "0.52"},
            ]
        },
        "Expansion": "2022-03-31",
    }
)

PERIOD_CHANGE = _j(
    {
        "Datas": [
            {"title": "近1周", "syl": "-2.31", "avg": "-1.12", "rank": "120", "sc": "1500"},
            {"title": "近1月", "syl": "3.21", "avg": "1.05", "rank": "80", "sc": "1500"},
        ]
    }
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

INDUSTRY = _j(
    {
        "Datas": [
            {"HYMC": "制造业", "ZJZBL": "45.2", "FSRQ": "2022-03-31", "SZ": "280.1"},
            {"HYMC": "金融业", "ZJZBL": "12.0", "FSRQ": "2022-03-31", "SZ": "74.5"},
        ]
    }
)

PUBLIC_DATES = _j({"Datas": ["2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31"]})

MANAGER_HTML = (
    "<html><body>"
    "<div class='bs_gl'>"
    "<label>基金经理</label>"
    "<a href='#'>王平</a><a href='#'>李强</a>"
    "</div>"
    "<span>2022-03-31</span>"
    "</body></html>"
).encode()


def _src(bodies: dict[str, bytes]) -> FundMobSource:
    return FundMobSource(client=FakeHttpClient(**bodies))


class TestFundBaseInfo:
    def test_parses(self) -> None:
        src = _src({"FundMNNBasicInformation": BASE_INFO})
        info = src.fetch_base_info("161725")
        assert info["code"] == "161725"
        assert info["name"] == "招商中证白酒指数(LOF)A"
        assert info["establish_date"] == "2015-05-27"
        assert info["change_pct"] == pytest.approx(-6.03)
        assert info["unit_nav"] == pytest.approx(1.1959)
        assert info["fund_company"] == "招商基金"
        assert info["nav_date"] == "2026-07-30"


class TestFundHoldings:
    def test_parses(self) -> None:
        src = _src({"FundMNInverstPosition": HOLDINGS})
        rows = src.fetch_holdings("161725")
        assert len(rows) == 2
        assert rows[0]["code"] == "600519"
        assert rows[0]["name"] == "贵州茅台"
        assert rows[0]["ratio"] == pytest.approx(16.78)
        assert rows[0]["change"] == "1.36"
        assert rows[0]["date"] == "2022-03-31"


class TestFundPeriodChange:
    def test_parses(self) -> None:
        src = _src({"FundMNPeriodIncrease": PERIOD_CHANGE})
        rows = src.fetch_period_change("161725")
        assert len(rows) == 2
        assert rows[0]["period"] == "近1周"
        assert rows[0]["return_rate"] == pytest.approx(-2.31)
        assert rows[0]["peer_avg"] == pytest.approx(-1.12)
        assert rows[0]["rank"] == 120
        assert rows[0]["peer_total"] == 1500
        assert rows[0]["fund_code"] == "161725"


class TestFundAssetAllocation:
    def test_parses(self) -> None:
        src = _src({"FundMNAssetAllocationNew": ASSET_ALLOC})
        rows = src.fetch_asset_allocation("161725")
        assert len(rows) == 1
        assert rows[0]["stock"] == pytest.approx(85.3)
        assert rows[0]["bond"] == pytest.approx(3.2)
        assert rows[0]["cash"] == pytest.approx(5.1)
        assert rows[0]["total_scale"] == pytest.approx(620.5)


class TestFundIndustryDistribution:
    def test_parses_and_dedups(self) -> None:
        src = _src({"FundMNSectorAllocation": INDUSTRY})
        rows = src.fetch_industry_distribution("161725")
        assert len(rows) == 2
        assert {r["industry"] for r in rows} == {"制造业", "金融业"}


class TestFundPublicDates:
    def test_parses(self) -> None:
        src = _src({"FundMNIVInfoMultiple": PUBLIC_DATES})
        dates = src.fetch_public_dates("161725")
        assert dates == ["2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31"]


class TestFundManager:
    def test_parses_best_effort(self) -> None:
        src = _src({"jjjl_161725.html": MANAGER_HTML})
        mgr = src.fetch_manager("161725")
        assert mgr is not None
        assert "王平" in mgr["manager"]
        assert mgr["appoint_date"] == "2022-03-31"
        assert mgr["fund_code"] == "161725"
