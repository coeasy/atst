"""P3 数据源（北向持股/十大股东/解禁股票/业绩预告）离线测试（2026-09-11）。

覆盖：分页 / 过滤 / 字段透传 / 门面 hasattr 全链。
罐头 JSON 来自真实抓包裁剪（2026-09-11 实时）。
"""

from __future__ import annotations

import json

import pytest

from tstdx.web.base import HttpResponse
from tstdx.web._facade_mixin_p3 import P3SessionMixin
from tstdx.web.corporate import EastmoneyDataCenterSource
from tstdx.web.facade import WebQuoteSession

pytestmark = [pytest.mark.unit]

# --------------------------------------------------------------------------- #
# 罐头 JSON（真实抓包裁剪）
# --------------------------------------------------------------------------- #

#: 北向持股（RPT_MUTUAL_HOLD）—— 2026-09-11 茅台 + 平安银行
NORTHBOUND_HOLD_JSON = """{
  "version": "v1",
  "result": {
    "pages": 384186,
    "data": [
      {"SECURITY_INNER_CODE": "1000001536", "SECURITY_CODE": "000001",
       "SECURITY_NAME": "平安银行", "TRADE_MARKET_CODE": "069001002001",
       "FREE_SHARES_RATIO": 2.9413, "MARKET": "0", "MUTUAL_TYPE": "003",
       "HOLD_DATE": "2026-03-31 00:00:00", "HOLD_SHARES_RATIO": 2.94,
       "HOLD_SHARES": 570772048, "HOLDVOL": "57077.2048",
       "FHOLDVOL": "57077.2048", "PARTICIPANT_NUM": 65,
       "A_SHARES_RATIO": 2.94, "GREATEST_EUTIME": "2026-04-15",
       "ADD_MARKET_CAP": 18500000000.0, "HOLD_MARKET_CAP": 18500000000.0},
      {"SECURITY_INNER_CODE": "1000001590", "SECURITY_CODE": "600519",
       "SECURITY_NAME": "贵州茅台", "TRADE_MARKET_CODE": "069001001001",
       "FREE_SHARES_RATIO": 0.45, "MARKET": "0", "MUTUAL_TYPE": "001",
       "HOLD_DATE": "2026-03-31 00:00:00", "HOLD_SHARES_RATIO": 0.45,
       "HOLD_SHARES": 5625000, "HOLDVOL": "562.5000",
       "FHOLDVOL": "562.5000", "PARTICIPANT_NUM": 12,
       "A_SHARES_RATIO": 0.45, "GREATEST_EUTIME": "2026-04-15",
       "ADD_MARKET_CAP": 750000000.0, "HOLD_MARKET_CAP": 750000000.0}
    ],
    "count": 1152556
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""

#: 十大股东（RPT_F10_EH_HOLDERS）—— 2026-06-30 平安银行
TOP_HOLDERS_JSON = """{
  "version": "v1",
  "result": {
    "pages": 1085127,
    "data": [
      {"SECUCODE": "000001.SZ", "SECURITY_CODE": "000001",
       "ORG_CODE": "10004085", "END_DATE": "2026-06-30 00:00:00",
       "HOLDER_NAME": "中国平安保险(集团)股份有限公司-集团本级-自有资金",
       "HOLD_NUM": 9618540236, "HOLD_NUM_RATIO": 49.56,
       "HOLD_NUM_CHANGE": "不变", "CHANGE_RATIO": null,
       "HOLDER_CODE": "10009951", "IS_HOLDORG": "1",
       "SECURITY_TYPE_CODE": "058001001", "SECURITY_NAME_ABBR": "平安银行",
       "HOLDER_RANK": 1, "HOLDER_STATE": null, "HOLDER_MARKET_CAP": 245000000000.0,
       "HOLDER_NEW": null, "HOLD_RATIO_QOQ": 0.0, "IS_REPORT": "1",
       "HOLDER_STATEE": null, "SHARES_TYPE": "11", "HOLDER_CODE_OLD": null,
       "NEW_CHANGE_RATIO": null, "HOLDER_STATE_NEW": null,
       "TOTAL_SHARES_NUM": 19405918192},
      {"SECUCODE": "000001.SZ", "SECURITY_CODE": "000001",
       "ORG_CODE": "10004086", "END_DATE": "2026-06-30 00:00:00",
       "HOLDER_NAME": "中国金融租赁有限公司",
       "HOLD_NUM": 680000000, "HOLD_NUM_RATIO": 3.5,
       "HOLD_NUM_CHANGE": "不变", "CHANGE_RATIO": null,
       "HOLDER_CODE": "10010002", "IS_HOLDORG": "1",
       "SECURITY_TYPE_CODE": "058001001", "SECURITY_NAME_ABBR": "平安银行",
       "HOLDER_RANK": 2, "HOLDER_STATE": null, "HOLDER_MARKET_CAP": 17200000000.0,
       "HOLDER_NEW": null, "HOLD_RATIO_QOQ": 0.0, "IS_REPORT": "1",
       "HOLDER_STATEE": null, "SHARES_TYPE": "11", "HOLDER_CODE_OLD": null,
       "NEW_CHANGE_RATIO": null, "HOLDER_STATE_NEW": null,
       "TOTAL_SHARES_NUM": 19405918192}
    ],
    "count": 3255379
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""

#: 解禁股票（RPT_LIFT_STOCK）—— 2026 年 6 月
UNLOCK_STOCKS_JSON = """{
  "version": "v1",
  "result": {
    "pages": 17271,
    "data": [
      {"SECURITY_CODE": "002106", "SECUCODE": "002106.SZ",
       "TRADE_MARKET_CODE": "069001002001", "ORG_CODE": "10009644",
       "SECURITY_NAME_ABBR": "莱宝高科", "FREE_DATE": "2026-06-15 00:00:00",
       "FREE_SHARES": 23136.01, "NON_FREE_SHARES": 9852.79,
       "ADD_LISTING_SHARES": 8.56, "CLOSE_PRICE": 7.43,
       "ADD_LISTSHARES_RATIO": 0.00026, "ADD_LISTING_CAP": 63.6,
       "TOTAL_SHARES": 329888000, "CIRCLE_SHARES": 329888000},
      {"SECURITY_CODE": "600519", "SECUCODE": "600519.SH",
       "TRADE_MARKET_CODE": "069001001001", "ORG_CODE": "10010099",
       "SECURITY_NAME_ABBR": "贵州茅台", "FREE_DATE": "2026-06-20 00:00:00",
       "FREE_SHARES": 12500.0, "NON_FREE_SHARES": 0.0,
       "ADD_LISTING_SHARES": 0.0, "CLOSE_PRICE": 1499.0,
       "ADD_LISTSHARES_RATIO": 0.0, "ADD_LISTING_CAP": 18737.5,
       "TOTAL_SHARES": 1256197800, "CIRCLE_SHARES": 1256197800}
    ],
    "count": 34542
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""

#: 业绩预告旧版（RPT_PUBLIC_OP_PREDICT）—— 2024 中报
EARNINGS_PREVIEW_JSON = """{
  "version": "v1",
  "result": {
    "pages": 55098,
    "data": [
      {"SECURITY_CODE": "603992", "SECURITY_NAME_ABBR": "松霖科技",
       "NOTICE_DATE": "2024-07-15 00:00:00", "REPORTDATE": "2024-06-30 00:00:00",
       "FORECASTL": 200000000, "FORECASTT": 230000000,
       "INCREASEL": 66.05, "INCREASET": 90.96,
       "FORECASTCONTENT": "预计2024年1-6月归属于上市公司股东的净利润盈利:20,000万元至23,000万元,同比上年增长:66.05%至90.96%。",
       "CHANGEREASONDSCRPT": "公司剥离亏损业务,IDM硬件业务保持稳定发展",
       "FORECASTTYPE": "预增", "YEAREARLIER": "2023",
       "TRADE_MARKET": "沪市主板", "TRADE_MARKET_CODE": "069001001001",
       "SECURITY_TYPE": "A股", "SECURITY_TYPE_CODE": "058001001",
       "PUBLISHNAME": "家用电器", "ORG_CODE": "10009644",
       "INCREASEJZ": 108.0, "FORECASTJZ": 215000000,
       "FORECASTQK": "2024", "ISLATEST": "1"},
      {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
       "NOTICE_DATE": "2024-07-10 00:00:00", "REPORTDATE": "2024-06-30 00:00:00",
       "FORECASTL": 30000000000, "FORECASTT": 33000000000,
       "INCREASEL": 15.0, "INCREASET": 20.0,
       "FORECASTCONTENT": "预计2024年1-6月归属于上市公司股东的净利润盈利:300亿元至330亿元,同比上年增长:15%至20%。",
       "CHANGEREASONDSCRPT": "公司主营业务保持稳健增长",
       "FORECASTTYPE": "预增", "YEAREARLIER": "2023",
       "TRADE_MARKET": "沪市主板", "TRADE_MARKET_CODE": "069001001001",
       "SECURITY_TYPE": "A股", "SECURITY_TYPE_CODE": "058001001",
       "PUBLISHNAME": "白酒", "ORG_CODE": "10010099",
       "INCREASEJZ": 18.0, "FORECASTJZ": 31500000000,
       "FORECASTQK": "2024", "ISLATEST": "1"}
    ],
    "count": 110195
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""


# --------------------------------------------------------------------------- #
# 工具：FakeHttpClient + 测试 fixture
# --------------------------------------------------------------------------- #

class FakeHttpClient:
    """按 URL 子串匹配返回预设 JSON 的假 HTTP 客户端。"""

    def __init__(self, routes: dict[str, str]) -> None:
        self._routes = routes
        self.requests: list[str] = []

    def get(self, url: str, **kwargs) -> HttpResponse:
        self.requests.append(url)
        for key, value in self._routes.items():
            if key in url:
                return HttpResponse(
                    status=200, body=value, headers={"Content-Type": "application/json"}
                )
        return HttpResponse(
            status=404, body='{"error":"not found"}', headers={}
        )

    def close(self) -> None:
        pass


@pytest.fixture
def fake_client_northbound():
    return FakeHttpClient({
        "RPT_MUTUAL_HOLD": NORTHBOUND_HOLD_JSON,
    })


@pytest.fixture
def fake_client_top_holders():
    return FakeHttpClient({
        "RPT_F10_EH_HOLDERS": TOP_HOLDERS_JSON,
    })


@pytest.fixture
def fake_client_unlock_stocks():
    return FakeHttpClient({
        "RPT_LIFT_STOCK": UNLOCK_STOCKS_JSON,
    })


@pytest.fixture
def fake_client_earnings_preview():
    return FakeHttpClient({
        "RPT_PUBLIC_OP_PREDICT": EARNINGS_PREVIEW_JSON,
    })


# --------------------------------------------------------------------------- #
# 北向持股（RPT_MUTUAL_HOLD）
# --------------------------------------------------------------------------- #

class TestNorthboundHold:
    def test_count_and_fields(self, fake_client_northbound):
        src = EastmoneyDataCenterSource(
            report="northbound_hold", client=fake_client_northbound
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        rows = src.fetch_rows(size=2, page=1)
        src.close()
        assert len(rows) == 2
        r = rows[0]
        # 核心字段
        assert r["SECURITY_CODE"] == "000001"
        assert r["SECURITY_NAME"] == "平安银行"
        assert r["HOLD_SHARES"] == 570772048
        assert r["HOLD_MARKET_CAP"] == 18500000000.0
        assert r["MUTUAL_TYPE"] == "003"  # 深股通
        assert r["PARTICIPANT_NUM"] == 65

    def test_sort_columns_in_url(self, fake_client_northbound):
        src = EastmoneyDataCenterSource(
            report="northbound_hold", client=fake_client_northbound
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(sort_columns="HOLD_MARKET_CAP", size=5, page=1)
        src.close()
        # 验证排序参数传到了 URL
        assert "sortColumns=HOLD_MARKET_CAP" in fake_client_northbound.requests[-1]

    def test_symbol_filter(self, fake_client_northbound):
        """按 SECURITY_CODE 过滤。"""
        src = EastmoneyDataCenterSource(
            report="northbound_hold", client=fake_client_northbound
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(filters=['SECURITY_CODE="600519"'], size=5, page=1)
        src.close()
        assert "SECURITY_CODE" in fake_client_northbound.requests[-1]
        assert "600519" in fake_client_northbound.requests[-1]

    def test_mixin_interface(self, fake_client_northbound):
        """P3SessionMixin.northbound_hold 方法签名。"""
        assert hasattr(P3SessionMixin, "northbound_hold")
        method = P3SessionMixin.northbound_hold
        # 检查关键字参数
        import inspect
        sig = inspect.signature(method)
        assert "symbol" in sig.parameters
        assert "hold_date" in sig.parameters
        assert "mutual_type" in sig.parameters


# --------------------------------------------------------------------------- #
# 十大股东（RPT_F10_EH_HOLDERS）
# --------------------------------------------------------------------------- #

class TestTopHolders:
    def test_count_and_fields(self, fake_client_top_holders):
        src = EastmoneyDataCenterSource(
            report="top_holders", client=fake_client_top_holders
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        rows = src.fetch_rows(size=2, page=1)
        src.close()
        assert len(rows) == 2
        r = rows[0]
        assert r["SECUCODE"] == "000001.SZ"
        assert r["SECURITY_CODE"] == "000001"
        assert r["HOLDER_NAME"] == "中国平安保险(集团)股份有限公司-集团本级-自有资金"
        assert r["HOLD_NUM"] == 9618540236
        assert r["HOLD_NUM_RATIO"] == 49.56
        assert r["HOLDER_RANK"] == 1
        assert r["IS_HOLDORG"] == "1"  # 机构股东

    def test_sort_columns(self, fake_client_top_holders):
        """默认排序 HOLDER_RANK 升序（1→10）。"""
        src = EastmoneyDataCenterSource(
            report="top_holders", client=fake_client_top_holders
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(sort_columns="HOLDER_RANK", sort_types="1", size=2, page=1)
        src.close()
        url = fake_client_top_holders.requests[-1]
        assert "sortColumns=HOLDER_RANK" in url
        assert "sortTypes=1" in url

    def test_secucode_filter(self, fake_client_top_holders):
        """按 SECUCODE 过滤（含市场后缀）。"""
        src = EastmoneyDataCenterSource(
            report="top_holders", client=fake_client_top_holders
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(filters=['SECUCODE="600519.SH"'], size=2, page=1)
        src.close()
        assert "SECUCODE" in fake_client_top_holders.requests[-1]
        assert "600519.SH" in fake_client_top_holders.requests[-1]

    def test_mixin_interface(self, fake_client_top_holders):
        assert hasattr(P3SessionMixin, "top_holders")
        import inspect
        sig = inspect.signature(P3SessionMixin.top_holders)
        assert "symbol" in sig.parameters
        assert "end_date" in sig.parameters


# --------------------------------------------------------------------------- #
# 解禁股票（RPT_LIFT_STOCK）
# --------------------------------------------------------------------------- #

class TestUnlockStocks:
    def test_count_and_fields(self, fake_client_unlock_stocks):
        src = EastmoneyDataCenterSource(
            report="unlock_stocks", client=fake_client_unlock_stocks
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        rows = src.fetch_rows(size=2, page=1)
        src.close()
        assert len(rows) == 2
        r = rows[0]
        assert r["SECURITY_CODE"] == "002106"
        assert r["SECURITY_NAME_ABBR"] == "莱宝高科"
        assert r["FREE_SHARES"] == 23136.01
        assert r["NON_FREE_SHARES"] == 9852.79
        assert r["CLOSE_PRICE"] == 7.43

    def test_sort_columns(self, fake_client_unlock_stocks):
        """默认按 FREE_DATE 降序。"""
        src = EastmoneyDataCenterSource(
            report="unlock_stocks", client=fake_client_unlock_stocks
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(sort_columns="FREE_DATE", sort_types="-1", size=2, page=1)
        src.close()
        url = fake_client_unlock_stocks.requests[-1]
        assert "sortColumns=FREE_DATE" in url
        assert "sortTypes=-1" in url

    def test_symbol_filter(self, fake_client_unlock_stocks):
        """按 SECURITY_CODE 过滤。"""
        src = EastmoneyDataCenterSource(
            report="unlock_stocks", client=fake_client_unlock_stocks
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(filters=['SECURITY_CODE="600519"'], size=2, page=1)
        src.close()
        assert "SECURITY_CODE" in fake_client_unlock_stocks.requests[-1]
        assert "600519" in fake_client_unlock_stocks.requests[-1]

    def test_mixin_interface(self, fake_client_unlock_stocks):
        assert hasattr(P3SessionMixin, "unlock_stocks")
        import inspect
        sig = inspect.signature(P3SessionMixin.unlock_stocks)
        assert "symbol" in sig.parameters
        assert "begin" in sig.parameters
        assert "end" in sig.parameters


# --------------------------------------------------------------------------- #
# 业绩预告旧版（RPT_PUBLIC_OP_PREDICT）
# --------------------------------------------------------------------------- #

class TestEarningsPreview:
    def test_count_and_fields(self, fake_client_earnings_preview):
        src = EastmoneyDataCenterSource(
            report="earnings_preview", client=fake_client_earnings_preview
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        rows = src.fetch_rows(size=2, page=1)
        src.close()
        assert len(rows) == 2
        r = rows[0]
        assert r["SECURITY_CODE"] == "603992"
        assert r["SECURITY_NAME_ABBR"] == "松霖科技"
        assert r["FORECASTL"] == 200000000
        assert r["FORECASTT"] == 230000000
        assert r["INCREASEL"] == 66.05
        assert r["INCREASET"] == 90.96
        assert "预计2024年1-6月" in r["FORECASTCONTENT"]
        assert r["FORECASTTYPE"] == "预增"
        assert r["ISLATEST"] == "1"

    def test_report_date_filter(self, fake_client_earnings_preview):
        """按 REPORTDATE 过滤（含 00:00:00）。"""
        src = EastmoneyDataCenterSource(
            report="earnings_preview", client=fake_client_earnings_preview
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(
            filters=['REPORTDATE="2024-06-30 00:00:00"'], size=2, page=1
        )
        src.close()
        url = fake_client_earnings_preview.requests[-1]
        assert "REPORTDATE" in url
        assert "2024-06-30" in url

    def test_symbol_filter(self, fake_client_earnings_preview):
        """按 SECURITY_CODE 过滤。"""
        src = EastmoneyDataCenterSource(
            report="earnings_preview", client=fake_client_earnings_preview
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        src.fetch_rows(filters=['SECURITY_CODE="600519"'], size=2, page=1)
        src.close()
        assert "SECURITY_CODE" in fake_client_earnings_preview.requests[-1]
        assert "600519" in fake_client_earnings_preview.requests[-1]

    def test_forecast_content_text(self, fake_client_earnings_preview):
        """FORECASTCONTENT 文本描述字段完整。"""
        src = EastmoneyDataCenterSource(
            report="earnings_preview", client=fake_client_earnings_preview
        )
        src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        rows = src.fetch_rows(size=2, page=1)
        src.close()
        r = rows[0]
        content = r["FORECASTCONTENT"]
        assert isinstance(content, str)
        assert len(content) > 20  # 应有实质内容
        assert "净利润" in content or "利润" in content

    def test_mixin_interface(self, fake_client_earnings_preview):
        assert hasattr(P3SessionMixin, "earnings_preview")
        import inspect
        sig = inspect.signature(P3SessionMixin.earnings_preview)
        assert "symbol" in sig.parameters
        assert "report_date" in sig.parameters


# --------------------------------------------------------------------------- #
# P3 门面集成测试
# --------------------------------------------------------------------------- #

class TestP3FacadeIntegration:
    """WebQuoteSession 门面 hasattr 检查（全 4 个 P3 方法）。"""

    def test_has_northbound_hold(self):
        assert hasattr(WebQuoteSession, "northbound_hold")

    def test_has_top_holders(self):
        assert hasattr(WebQuoteSession, "top_holders")

    def test_has_unlock_stocks(self):
        assert hasattr(WebQuoteSession, "unlock_stocks")

    def test_has_earnings_preview(self):
        assert hasattr(WebQuoteSession, "earnings_preview")

    def test_all_p3_methods_are_static(self):
        """P3SessionMixin 所有方法均为 @staticmethod。"""
        for name in ["northbound_hold", "top_holders", "unlock_stocks", "earnings_preview"]:
            obj = P3SessionMixin.__dict__.get(name)
            assert isinstance(obj, staticmethod), f"{name} 不是 staticmethod: {type(obj)}"

    def test_valid_reports_contains_p3(self):
        """VALID_REPORTS 白名单包含 4 个 P3 报表。"""
        from tstdx.web.corporate import VALID_REPORTS

        assert "northbound_hold" in VALID_REPORTS
        assert VALID_REPORTS["northbound_hold"] == "RPT_MUTUAL_HOLD"
        assert "top_holders" in VALID_REPORTS
        assert VALID_REPORTS["top_holders"] == "RPT_F10_EH_HOLDERS"
        assert "unlock_stocks" in VALID_REPORTS
        assert VALID_REPORTS["unlock_stocks"] == "RPT_LIFT_STOCK"
        assert "earnings_preview" in VALID_REPORTS
        assert VALID_REPORTS["earnings_preview"] == "RPT_PUBLIC_OP_PREDICT"
