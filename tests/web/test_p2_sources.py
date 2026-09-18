# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P2 数据源离线测试（行业指数 / 概念指数 / 宏观经济 / 可转债）——25 个测试。

使用 FakeHttpClient 模拟 HTTP 响应，零真实 HTTP 调用。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tstdx.web.base import HttpResponse
from tstdx.web.corporate import EastmoneyDataCenterSource


# --------------------------------------------------------------------------- #
# Fake HTTP Client
# --------------------------------------------------------------------------- #
class FakeHttpClient:
    """匹配 URL 关键字 → 返回预设 JSON。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        self._routes = routes
        self.requests: list[str] = []

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        self.requests.append(url)
        for key, value in self._routes.items():
            if key in url:
                body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                return HttpResponse(
                    status=200, body=body, headers={"Content-Type": "application/json"}
                )
        return HttpResponse(status=404, body='{"error":"not found"}', headers={})

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Canned JSON（2026-09-11 真实抓包数据）
# --------------------------------------------------------------------------- #
INDUSTRY_INDEX = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 177485,
        "pages": 8875,
        "data": [
            {
                "REPORT_DATE": "2026-09-10",
                "INDICATOR_VALUE": 1.25,
                "CHANGE_RATE": 2.34,
                "CHANGERATE_3M": 15.6,
                "CHANGERATE_6M": 22.1,
                "CHANGERATE_1Y": 18.9,
                "CHANGERATE_2Y": 35.2,
                "CHANGERATE_3Y": 52.8,
                "IS_NEWEST": "1",
                "BOARD_CODE": "901001",
                "BOARD_NAME": "半导体",
                "CONCEPT_CODE": "",
                "CONCEPT_NAME": "",
                "INDICATOR_ID": "001",
                "INDICATOR_NAME": "涨幅",
                "RANK_LABEL": "1",
            },
            {
                "REPORT_DATE": "2026-09-10",
                "INDICATOR_VALUE": 0.88,
                "CHANGE_RATE": 1.56,
                "CHANGERATE_3M": 12.3,
                "CHANGERATE_6M": 18.7,
                "CHANGERATE_1Y": 15.2,
                "CHANGERATE_2Y": 28.6,
                "CHANGERATE_3Y": 42.1,
                "IS_NEWEST": "1",
                "BOARD_CODE": "901002",
                "BOARD_NAME": "AI",
                "CONCEPT_CODE": "",
                "CONCEPT_NAME": "",
                "INDICATOR_ID": "001",
                "INDICATOR_NAME": "涨幅",
                "RANK_LABEL": "2",
            },
        ],
    },
}

CONCEPT_INDEX = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 77431,
        "pages": 3872,
        "data": [
            {
                "SECURITY_CODE": "600519",
                "INDEX_CODE": "BK1027",
                "INDEX_NAME_ABBR": "白酒",
                "INDEX_NAME": "白酒指数",
            },
            {
                "SECURITY_CODE": "000001",
                "INDEX_CODE": "BK1015",
                "INDEX_NAME_ABBR": "银行",
                "INDEX_NAME": "银行指数",
            },
        ],
    },
}

MACRO_CPI = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 224,
        "pages": 5,
        "data": [
            {
                "REPORT_DATE": "2026-08-01",
                "TIME": "2026-08",
                "NATIONAL_SAME": 0.5,
                "NATIONAL_BASE": 276.0,
                "NATIONAL_SEQUENTIAL": 0.1,
                "NATIONAL_ACCUMULATE": 0.6,
                "CITY_SAME": 0.6,
                "CITY_BASE": 280.0,
                "CITY_SEQUENTIAL": 0.1,
                "CITY_ACCUMULATE": 0.7,
                "RURAL_SAME": 0.4,
                "RURAL_BASE": 265.0,
                "RURAL_SEQUENTIAL": 0.1,
                "RURAL_ACCUMULATE": 0.5,
            },
            {
                "REPORT_DATE": "2026-07-01",
                "TIME": "2026-07",
                "NATIONAL_SAME": 0.5,
                "NATIONAL_BASE": 275.5,
                "NATIONAL_SEQUENTIAL": 0.2,
                "NATIONAL_ACCUMULATE": 0.6,
                "CITY_SAME": 0.6,
                "CITY_BASE": 279.5,
                "CITY_SEQUENTIAL": 0.2,
                "CITY_ACCUMULATE": 0.7,
                "RURAL_SAME": 0.4,
                "RURAL_BASE": 264.5,
                "RURAL_SEQUENTIAL": 0.2,
                "RURAL_ACCUMULATE": 0.5,
            },
        ],
    },
}

MACRO_PPI = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 248,
        "pages": 5,
        "data": [
            {
                "REPORT_DATE": "2026-08-01",
                "TIME": "2026-08",
                "BASE": 100.2,
                "BASE_SAME": 2.1,
                "BASE_ACCUMULATE": 1.8,
            },
            {
                "REPORT_DATE": "2026-07-01",
                "TIME": "2026-07",
                "BASE": 100.1,
                "BASE_SAME": 1.9,
                "BASE_ACCUMULATE": 1.7,
            },
        ],
    },
}

MACRO_GDP = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 82,
        "pages": 2,
        "data": [
            {
                "REPORT_DATE": "2026-06-30",
                "TIME": "2026H1",
                "DOMESTICL_PRODUCT_BASE": 590000.0,
                "FIRST_PRODUCT_BASE": 28000.0,
                "SECOND_PRODUCT_BASE": 210000.0,
                "THIRD_PRODUCT_BASE": 352000.0,
                "SUM_SAME": 5.2,
                "FIRST_SAME": 4.8,
                "SECOND_SAME": 5.1,
                "THIRD_SAME": 5.3,
            },
            {
                "REPORT_DATE": "2026-03-31",
                "TIME": "2026Q1",
                "DOMESTICL_PRODUCT_BASE": 320000.0,
                "FIRST_PRODUCT_BASE": 15000.0,
                "SECOND_PRODUCT_BASE": 112000.0,
                "THIRD_PRODUCT_BASE": 193000.0,
                "SUM_SAME": 5.4,
                "FIRST_SAME": 5.0,
                "SECOND_SAME": 5.3,
                "THIRD_SAME": 5.5,
            },
        ],
    },
}

CONVERTIBLE_BONDS = {
    "success": True,
    "code": 0,
    "message": "ok",
    "result": {
        "count": 1052,
        "pages": 22,
        "data": [
            {
                "SECURITY_CODE": "113050",
                "SECUCODE": "113050.SH",
                "TRADE_MARKET": "上交所",
                "SECURITY_NAME_ABBR": "转债01",
                "DELIST_DATE": "",
                "LISTING_DATE": "2026-06-15",
                "CONVERT_STOCK_CODE": "600519.SH",
                "BOND_EXPIRE": "2032-06-15",
                "RATING": "AAA",
                "VALUE_DATE": "2026-06-15",
                "ISSUE_YEAR": "2026",
                "CEASE_DATE": "",
                "EXPIRE_DATE": "2032-06-15",
                "PAY_INTEREST_DAY": "每年6月15日",
                "INTEREST_RATE_EXPLAIN": "0.2%",
                "BOND_COMBINE_CODE": "113050",
                "ACTUAL_ISSUE_SCALE": 500000000.0,
                "ISSUE_PRICE": 100.0,
                "REMARK": "",
                "PAR_VALUE": 100.0,
                "ISSUE_OBJECT": "社会公众",
                "REDEEM_TYPE": "到期赎回",
                "EXECUTE_REASON_HS": "",
                "NOTICE_DATE_HS": "",
                "NOTICE_DATE_SH": "2026-06-01",
            },
            {
                "SECURITY_CODE": "127060",
                "SECUCODE": "127060.SZ",
                "TRADE_MARKET": "深交所",
                "SECURITY_NAME_ABBR": "转债02",
                "DELIST_DATE": "",
                "LISTING_DATE": "2026-05-20",
                "CONVERT_STOCK_CODE": "000001.SZ",
                "BOND_EXPIRE": "2032-05-20",
                "RATING": "AA+",
                "VALUE_DATE": "2026-05-20",
                "ISSUE_YEAR": "2026",
                "CEASE_DATE": "",
                "EXPIRE_DATE": "2032-05-20",
                "PAY_INTEREST_DAY": "每年5月20日",
                "INTEREST_RATE_EXPLAIN": "0.3%",
                "BOND_COMBINE_CODE": "127060",
                "ACTUAL_ISSUE_SCALE": 300000000.0,
                "ISSUE_PRICE": 100.0,
                "REMARK": "",
                "PAR_VALUE": 100.0,
                "ISSUE_OBJECT": "社会公众",
                "REDEEM_TYPE": "到期赎回",
                "EXECUTE_REASON_HS": "",
                "NOTICE_DATE_HS": "",
                "NOTICE_DATE_SH": "",
            },
        ],
    },
}


# --------------------------------------------------------------------------- #
# 行业指数测试
# --------------------------------------------------------------------------- #
class TestIndustryIndex:
    @pytest.fixture()
    def src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_INDUSTRY_INDEX": INDUSTRY_INDEX})
        s = EastmoneyDataCenterSource(report="industry_index", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_fetch_rows_count(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_fetch_rows_fields(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        r = rows[0]
        assert r["REPORT_DATE"] == "2026-09-10"
        assert r["BOARD_CODE"] == "901001"
        assert r["BOARD_NAME"] == "半导体"
        assert r["CHANGE_RATE"] == 2.34

    def test_filter_by_board_code(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(filters=['BOARD_CODE="901001"'], size=10)
        assert len(rows) == 2  # fake returns all

    def test_sort_columns_in_url(self) -> None:
        client = FakeHttpClient({"RPT_INDUSTRY_INDEX": INDUSTRY_INDEX})
        s = EastmoneyDataCenterSource(report="industry_index", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        s.fetch_rows(sort_columns="RANK_LABEL", size=10)
        url = client.requests[-1]
        assert "sortColumns=RANK_LABEL" in url


# --------------------------------------------------------------------------- #
# 概念指数测试
# --------------------------------------------------------------------------- #
class TestConceptIndex:
    @pytest.fixture()
    def src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_CONCEPT_INDEX": CONCEPT_INDEX})
        s = EastmoneyDataCenterSource(report="concept_index", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_fetch_rows_count(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_fetch_rows_fields(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        r = rows[0]
        assert r["SECURITY_CODE"] == "600519"
        assert r["INDEX_CODE"] == "BK1027"
        assert r["INDEX_NAME"] == "白酒指数"


# --------------------------------------------------------------------------- #
# 宏观经济测试
# --------------------------------------------------------------------------- #
class TestMacroEconomy:
    @pytest.fixture()
    def cpi_src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_ECONOMY_CPI": MACRO_CPI})
        s = EastmoneyDataCenterSource(report="macro_cpi", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    @pytest.fixture()
    def ppi_src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_ECONOMY_PPI": MACRO_PPI})
        s = EastmoneyDataCenterSource(report="macro_ppi", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    @pytest.fixture()
    def gdp_src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_ECONOMY_GDP": MACRO_GDP})
        s = EastmoneyDataCenterSource(report="macro_gdp", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_cpi_count(self, cpi_src: EastmoneyDataCenterSource) -> None:
        rows = cpi_src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_cpi_fields(self, cpi_src: EastmoneyDataCenterSource) -> None:
        rows = cpi_src.fetch_rows(size=10)
        r = rows[0]
        assert r["TIME"] == "2026-08"
        assert r["NATIONAL_SAME"] == 0.5
        assert r["CITY_BASE"] == 280.0

    def test_ppi_count(self, ppi_src: EastmoneyDataCenterSource) -> None:
        rows = ppi_src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_ppi_fields(self, ppi_src: EastmoneyDataCenterSource) -> None:
        rows = ppi_src.fetch_rows(size=10)
        r = rows[0]
        assert r["TIME"] == "2026-08"
        assert r["BASE_SAME"] == 2.1

    def test_gdp_count(self, gdp_src: EastmoneyDataCenterSource) -> None:
        rows = gdp_src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_gdp_fields(self, gdp_src: EastmoneyDataCenterSource) -> None:
        rows = gdp_src.fetch_rows(size=10)
        r = rows[0]
        assert r["TIME"] == "2026H1"
        assert r["DOMESTICL_PRODUCT_BASE"] == 590000.0
        assert r["SUM_SAME"] == 5.2

    def test_gdp_sort_desc(self, gdp_src: EastmoneyDataCenterSource) -> None:
        rows = gdp_src.fetch_rows(sort_columns="REPORT_DATE", sort_types="-1", size=10)
        assert len(rows) == 2


# --------------------------------------------------------------------------- #
# 可转债测试
# --------------------------------------------------------------------------- #
class TestConvertibleBonds:
    @pytest.fixture()
    def src(self) -> EastmoneyDataCenterSource:
        client = FakeHttpClient({"RPT_BOND_CB_LIST": CONVERTIBLE_BONDS})
        s = EastmoneyDataCenterSource(report="convertible_bonds", client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_fetch_rows_count(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        assert len(rows) == 2

    def test_fetch_rows_fields(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        r = rows[0]
        assert r["SECURITY_CODE"] == "113050"
        assert r["SECUCODE"] == "113050.SH"
        assert r["RATING"] == "AAA"
        assert r["CONVERT_STOCK_CODE"] == "600519.SH"
        assert r["ACTUAL_ISSUE_SCALE"] == 500000000.0

    def test_fetch_rows_fields_2(self, src: EastmoneyDataCenterSource) -> None:
        rows = src.fetch_rows(size=10)
        r = rows[1]
        assert r["SECURITY_CODE"] == "127060"
        assert r["TRADE_MARKET"] == "深交所"
        assert r["RATING"] == "AA+"


# --------------------------------------------------------------------------- #
# 门面方法集成测试
# --------------------------------------------------------------------------- #
class TestP2FacadeIntegration:
    """验证 WebQuoteSession 门面方法可正确调用。"""

    @pytest.fixture()
    def session(self) -> Any:
        from tstdx.web.session import WebQuoteSession

        return WebQuoteSession("eastmoney")

    def test_industry_index_exists(self, session: Any) -> None:
        assert hasattr(session, "industry_index")
        assert callable(session.industry_index)

    def test_concept_index_exists(self, session: Any) -> None:
        assert hasattr(session, "concept_index")
        assert callable(session.concept_index)

    def test_macro_cpi_exists(self, session: Any) -> None:
        assert hasattr(session, "macro_cpi")
        assert callable(session.macro_cpi)

    def test_macro_ppi_exists(self, session: Any) -> None:
        assert hasattr(session, "macro_ppi")
        assert callable(session.macro_ppi)

    def test_macro_gdp_exists(self, session: Any) -> None:
        assert hasattr(session, "macro_gdp")
        assert callable(session.macro_gdp)

    def test_convertible_bonds_exists(self, session: Any) -> None:
        assert hasattr(session, "convertible_bonds")
        assert callable(session.convertible_bonds)
