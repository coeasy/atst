"""astock-data-toolkit 对标扩展源（估值 / 分红 / 增减持 / 财务摘要 / 公告）离线测试。

全部用罐头 JSON，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from tstdx.web.astock_toolkit import (
    EastmoneyAnnouncementSource,
    EastmoneyDividendSource,
    EastmoneyFinanceMainSource,
    EastmoneyHolderChangeSource,
    EastmoneyValuationSource,
)
from tstdx.web.base import HttpResponse

_J = "https://datacenter-web.eastmoney.com"


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


DIVIDEND = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2025-12-31",
                    "BONUS_SHARES_RATIO": "0",
                    "TRANSFER_SHARES_RATIO": "0",
                    "CASH_DIVIDEND_RATIO": "276.24",
                    "EX_DIVIDEND_DATE": "2026-06-26",
                    "RECORD_DATE": "2026-06-25",
                    "DIVIDEND_DATE": "2026-06-26",
                    "PROGRESS": "实施",
                }
            ],
            "pages": 1,
        },
    }
)

VALUATION = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-03-31",
                    "PE_TTM": 22.5,
                    "PB": 8.1,
                    "PS_TTM": 10.2,
                    "PCF_TTM": 23.4,
                    "TOTAL_SHARE": 1256197800,
                    "TOTAL_MV": 198800000000,
                }
            ],
            "pages": 1,
        },
    }
)

HOLDER_CHANGES = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "300750",
                    "SECURITY_NAME_ABBR": "宁德时代",
                    "PERSON_NAME": "李某",
                    "POSITION": "董事",
                    "CHANGE_SHARES": -120000,
                    "AVG_PRICE": 180.5,
                    "HOLD_SHARES_AFTER": 980000,
                    "CHANGE_RATIO": 0.025,
                    "CHANGE_REASON": "个人资金需求",
                    "CHANGE_DATE": "2026-08-01",
                    "DISCLOSURE_DATE": "2026-08-03",
                }
            ],
            "pages": 1,
        },
    }
)

FINANCE_MAIN = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-03-31",
                    "BASICEPS": 19.5,
                    "ROE": 12.3,
                    "BVEPS": 160.2,
                    "TOTAL_OPERATE_INCOME": 50000000000,
                    "PARENT_NETPROFIT": 25000000000,
                    "OPERATE_INCOME_YOY": 12.0,
                    "PARENT_NETPROFIT_YOY": 15.0,
                    "GROSS_MARGIN": 91.5,
                    "DEBT_ASSET_RATIO": 20.1,
                }
            ],
            "pages": 1,
        },
    }
)

NOTICES = _j(
    {
        "data": {
            "list": [
                {
                    "art_code": "ABC123",
                    "title": "关于2026年半年度报告",
                    "notice_date": "2026-08-15 00:00:00",
                    "display_time": "2026-08-14 20:41:29",
                    "columns": [{"column_name": "年报"}],
                    "codes": [{"stock_code": "600519"}],
                }
            ]
        }
    }
)


def _div() -> EastmoneyDividendSource:
    return EastmoneyDividendSource(client=FakeHttpClient(RPT_SHAREBONUS_DET=DIVIDEND))


def _val() -> EastmoneyValuationSource:
    return EastmoneyValuationSource(client=FakeHttpClient(RPT_VALUEASSESS_DET=VALUATION))


def _hc() -> EastmoneyHolderChangeSource:
    return EastmoneyHolderChangeSource(
        client=FakeHttpClient(RPT_CAPITAL_PARTICIPATION_DET=HOLDER_CHANGES)
    )


def _fin() -> EastmoneyFinanceMainSource:
    return EastmoneyFinanceMainSource(client=FakeHttpClient(RPT_F10_FINANCE_MAIN=FINANCE_MAIN))


def _ann() -> EastmoneyAnnouncementSource:
    return EastmoneyAnnouncementSource(client=FakeHttpClient(ann=NOTICES))


class TestDividend:
    def test_parses(self) -> None:
        rows = _div().fetch_dividend("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "600519"
        assert r["name"] == "贵州茅台"
        assert r["cash_dividend_per_10"] == pytest.approx(276.24)
        assert r["ex_dividend_date"] == "2026-06-26"
        assert r["progress"] == "实施"


class TestValuation:
    def test_parses(self) -> None:
        rows = _val().fetch_valuation("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["pe_ttm"] == pytest.approx(22.5)
        assert r["pb"] == pytest.approx(8.1)
        assert r["ps_ttm"] == pytest.approx(10.2)
        assert r["total_share"] == pytest.approx(1256197800)
        assert r["total_mv"] == pytest.approx(1.988e11)


class TestHolderChanges:
    def test_parses(self) -> None:
        rows = _hc().fetch_holder_changes("300750")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "300750"
        assert r["person"] == "李某"
        assert r["change_shares"] == pytest.approx(-120000)
        assert r["avg_price"] == pytest.approx(180.5)
        assert r["change_date"] == "2026-08-01"


class TestFinanceMain:
    def test_parses(self) -> None:
        rows = _fin().fetch_main_indicators("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["eps"] == pytest.approx(19.5)
        assert r["roe"] == pytest.approx(12.3)
        assert r["revenue"] == pytest.approx(5e10)
        assert r["net_profit_yoy"] == pytest.approx(15.0)
        assert r["gross_margin"] == pytest.approx(91.5)


class TestAnnouncements:
    def test_parses(self) -> None:
        rows = _ann().fetch_notices(["600519"])
        assert len(rows) == 1
        r = rows[0]
        assert r["art_code"] == "ABC123"
        assert r["title"] == "关于2026年半年度报告"
        assert r["codes"] == ["600519"]


class TestFacadeMixin:
    """整链离线验证：monkeypatch 共享 HTTP 客户端，驱动 AstockToolkitMixin。"""

    def test_all_methods(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.web._session_astock as mix

        bodies = {
            "RPT_SHAREBONUS_DET": DIVIDEND,
            "RPT_VALUEASSESS_DET": VALUATION,
            "RPT_CAPITAL_PARTICIPATION_DET": HOLDER_CHANGES,
            "RPT_F10_FINANCE_MAIN": FINANCE_MAIN,
            "ann": NOTICES,
        }
        monkeypatch.setattr(mix, "_shared_http", lambda: FakeHttpClient(**bodies))

        assert len(mix.AstockToolkitMixin.dividend_history("600519")) == 1
        assert len(mix.AstockToolkitMixin.stock_valuation("600519")) == 1
        assert len(mix.AstockToolkitMixin.holder_changes("300750")) == 1
        assert len(mix.AstockToolkitMixin.financial_abstract("600519")) == 1
        assert len(mix.AstockToolkitMixin.announcements(["600519"])) == 1
