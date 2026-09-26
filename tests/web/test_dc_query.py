"""通用 datacenter 报表直查（dc_query/dc_reports）测试（P12 扩展）。

覆盖：白名单暴露 / symbol→filter 自动构造 / filters 透传 /
fetch_rows 委托参数 / 真实罐头解析（dividend 报表，字段原样透传）。
"""

from __future__ import annotations

import json

import pytest

from tstdx.web._session_info import CorporateSessionMixin
from tstdx.web.corporate import VALID_REPORTS, EastmoneyDataCenterSource
from tstdx.web.eastmoney.adapters import EastmoneyMarginSource  # noqa: F401

pytestmark = pytest.mark.unit

#: 罐头：dividend 报表真实响应裁剪（2026-09-06 茅台）
DIVIDEND_JSON = """{
  "version": "v1",
  "result": {
    "pages": 12,
    "data": [
      {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
       "SECUCODE": "600519.SH", "REPORT_DATE": "2025-12-31 00:00:00",
       "PUBLISH_DATE": "2026-04-17 00:00:00",
       "PRETAX_BONUS_RMB": 280.2423, "TOTAL_SHARES": 1250081601},
      {"SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "贵州茅台",
       "SECUCODE": "600519.SH", "REPORT_DATE": "2025-06-30 00:00:00",
       "PUBLISH_DATE": "2025-08-09 00:00:00",
       "PRETAX_BONUS_RMB": 23.882, "TOTAL_SHARES": 1256197800}
    ],
    "count": 2
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""


class TestDcReports:
    def test_whitelist_contains_dividend(self):
        reports = CorporateSessionMixin.dc_reports()
        assert reports["dividend"] == "RPT_SHAREBONUS_DET"
        assert reports == VALID_REPORTS

    def test_symbol_filter_key_table_covers_whitelist(self):
        """除 ipo 外，白名单报表都应有个股过滤键（防 dc_query 静默丢 symbol）。"""
        for name in VALID_REPORTS:
            if name == "ipo":
                continue
            assert name in CorporateSessionMixin._DC_SYMBOL_FILTER_KEYS


class TestDcQuery:
    def test_symbol_becomes_filter(self, monkeypatch):
        captured: dict = {}

        def fake_fetch_rows(
            self,
            *,
            filters=(),
            sort_columns="",
            sort_types="-1",
            page=1,
            size=20,
            report=None,
            all_pages=False,
            max_pages=50,
        ):
            captured.update(filters=list(filters), page=page, size=size, sort_columns=sort_columns)
            return json.loads(DIVIDEND_JSON)["result"]["data"]

        monkeypatch.setattr(EastmoneyDataCenterSource, "fetch_rows", fake_fetch_rows)
        rows = CorporateSessionMixin.dc_query(
            "dividend", symbol="sh600519", sort_columns="PUBLISH_DATE", size=5
        )
        assert captured["filters"] == ['SECURITY_CODE="600519"']
        assert captured["sort_columns"] == "PUBLISH_DATE"
        assert captured["size"] == 5
        # 字段原样透传（不重命名/不翻译）
        assert rows[0]["SECURITY_CODE"] == "600519"
        assert rows[0]["PRETAX_BONUS_RMB"] == pytest.approx(280.2423)
        assert rows[1]["REPORT_DATE"].startswith("2025-06-30")

    def test_explicit_filters_passthrough(self, monkeypatch):
        captured: dict = {}

        def fake_fetch_rows(self, *, filters=(), **kw):
            captured["filters"] = list(filters)
            return []

        monkeypatch.setattr(EastmoneyDataCenterSource, "fetch_rows", fake_fetch_rows)
        CorporateSessionMixin.dc_query("performance", filters=['REPORT_DATE="2025-12-31"'])
        assert captured["filters"] == ['REPORT_DATE="2025-12-31"']

    def test_symbol_ignored_for_reports_without_key(self, monkeypatch):
        """ipo 无个股过滤键：symbol 传入也不构造 filter（而非拼错字段）。"""
        captured: dict = {}

        def fake_fetch_rows(self, *, filters=(), **kw):
            captured["filters"] = list(filters)
            return []

        monkeypatch.setattr(EastmoneyDataCenterSource, "fetch_rows", fake_fetch_rows)
        CorporateSessionMixin.dc_query("ipo", symbol="sh600519")
        assert captured["filters"] == []
