"""astock-data-toolkit 对标扩展源（估值 / 分红 / 增减持 / 财务摘要 / 公告）离线测试。

全部用罐头 JSON，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from atst.web.astock_toolkit import (
    EastmoneyAnnouncementSource,
    EastmoneyDividendSource,
    EastmoneyFinanceMainSource,
    EastmoneyHolderChangeSource,
    EastmoneyRightsIssueSource,
    EastmoneyValuationSource,
)
from atst.web.base import HttpResponse

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
        import atst.web._session_astock as mix

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


class TestValuationHistoryBounds:
    def test_count_rejects_invalid_values_before_request(self, monkeypatch: pytest.MonkeyPatch):
        import atst.web._session_gaps as gaps

        calls = []
        monkeypatch.setattr(gaps, "_eastmoney_rows", lambda *args, **kwargs: calls.append(kwargs))
        for count in (0, -1, 501, True):
            with pytest.raises(ValueError, match="count"):
                gaps.GapsSessionMixin.valuation_history("600519", count=count)
        assert calls == []

    def test_valid_count_is_forwarded(self, monkeypatch: pytest.MonkeyPatch):
        import atst.web._session_gaps as gaps

        calls = []

        def fake_rows(report, **kwargs):
            calls.append((report, kwargs))
            return [{"TRADE_DATE": "2026-10-01"}]

        monkeypatch.setattr(gaps, "_eastmoney_rows", fake_rows)
        result = gaps.GapsSessionMixin.valuation_history("sh600519", count=250)
        assert result == [{"TRADE_DATE": "2026-10-01"}]
        assert calls[0][0] == "RPT_VALUEANALYSIS_DET"
        assert calls[0][1]["size"] == 250


RIGHTS_ISSUE = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "601398",
                    "SECURITY_NAME_ABBR": "工商银行",
                    "EX_DIVIDEND_DATE": "2010-11-24",
                    "EQUITY_RECORD_DATE": "2010-11-15",
                    "PLACING_RATIO": 0.45,
                    "ISSUE_PRICE": 2.99,
                    "LISTING_DATE": "2010-11-30",
                }
            ],
            "pages": 1,
        },
    }
)

#: 稀疏报表的"没有记录"应答：``success=false`` + ``code=9201``，不是故障。
EMPTY_RESULT = _j({"success": False, "code": 9201, "message": "返回数据为空"})


def _rights(*, empty: bool = False) -> EastmoneyRightsIssueSource:
    body = EMPTY_RESULT if empty else RIGHTS_ISSUE
    return EastmoneyRightsIssueSource(client=FakeHttpClient(RPT_IPO_ALLOTMENT=body))


class TestRightsIssue:
    """配股（``RPT_IPO_ALLOTMENT``）：分红表里没有的那一半事件。"""

    def test_parses_real_columns(self) -> None:
        rows = _rights().fetch_rights_issue("sh601398")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "601398"
        assert r["ex_dividend_date"] == "2010-11-24"
        assert r["rights_ratio_per_10"] == pytest.approx(0.45)
        assert r["rights_price"] == pytest.approx(2.99)

    def test_empty_result_is_normal_for_a_sparse_report(self) -> None:
        #: 绝大多数标的从未配股。若把 9201 当故障抛，"没配过股"就成了一次调用失败。
        assert _rights(empty=True).fetch_rights_issue("sh600519") == []

    def test_empty_result_still_raises_without_the_explicit_opt_in(self) -> None:
        #: 开关是显式的：默认（不传 allow_empty）照旧抛，免得真故障被当空集吞掉。
        from atst.web.corporate import EMPTY_RESULT_CODE, is_empty_result

        payload = json.loads(EMPTY_RESULT)
        assert payload["code"] == EMPTY_RESULT_CODE
        assert is_empty_result(payload) is True
        assert is_empty_result({"success": True, "result": {"data": []}}) is False
        assert is_empty_result({"success": False, "code": 9501}) is False
        assert is_empty_result("不是 JSON") is False

    def test_code_may_arrive_as_string(self) -> None:
        #: 上游有时把 code 发成字符串；判据不能只认 int。
        from atst.web.corporate import is_empty_result

        assert is_empty_result({"success": False, "code": "9201"}) is True


class TestMarginEmptyOptIn:
    """``EastmoneyMarginSource.parse_rows`` 接了 ``allow_empty`` 就必须真的用它。"""

    def test_empty_payload_returns_no_rows(self) -> None:
        from atst.web.eastmoney.adapters import EastmoneyMarginSource

        src = EastmoneyMarginSource(client=FakeHttpClient())
        assert src.parse_rows(json.loads(EMPTY_RESULT), allow_empty=True) == []
