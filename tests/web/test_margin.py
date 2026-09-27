"""东财融资融券个股明细源测试（P12 扩展）。

覆盖：datacenter JSON 罐头解析与字段归一化（金额单位元量纲照抄）/
days 截断 / 非标的空列表 / 非 JSON 显式 SourceDeprecated /
注册表三方一致性（margin 进 _ADAPTERS + normalizer + facade 方法）。
"""

from __future__ import annotations

import pytest

from atst.errors import SourceDeprecated
from atst.web import _ADAPTERS
from atst.web.eastmoney.adapters import EastmoneyMarginSource
from atst.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit

#: 罐头：datacenter-web 真实响应裁剪（2026-09-04 茅台两融行）
MARGIN_JSON = """{
  "version": "8e3f4aef",
  "result": {
    "pages": 1325,
    "data": [
      {"DATE": "2026-09-04 00:00:00", "MARKET": "融资融券_沪证",
       "SCODE": "600519", "SECNAME": "贵州茅台", "SECUCODE": "600519.SH",
       "RZYE": 17034742324, "RQYL": 117919, "RZRQYE": 17191574594,
       "RQYE": 156832270, "RQMCL": 8500, "RZRQYECZ": 16877910054,
       "RZMRE": 321312662, "SZ": 1662608529330, "RZYEZB": 1.02457927,
       "SPJ": 1421.0, "ZDF": 0.87, "RZMRE3D": 633591142,
       "TRADE_MARKET": "上海证券交易所", "TRADE_MARKET_CODE": "1", "KCB": 0},
      {"DATE": "2026-09-03 00:00:00", "MARKET": "融资融券_沪证",
       "SCODE": "600519", "SECNAME": "贵州茅台", "RZYE": 16999888888,
       "RZMRE": 300000000, "RQYE": 156000000, "RZRQYE": 17155888888,
       "RQMCL": 8000, "RQYL": 110000, "RZCHE": 100000000,
       "RZJME": 200000000, "RZRQYECZ": 17000000000, "RZYEZB": 1.02,
       "SZ": 165000000000, "SPJ": 1408.0, "ZDF": 0.5}
    ],
    "count": 2
  },
  "success": true,
  "message": "ok",
  "code": 0
}"""


class TestMarginParse:
    def test_parse_normalizes_fields(self):
        src = EastmoneyMarginSource()
        rows = src.parse_margin(MARGIN_JSON)
        assert len(rows) == 2
        r = rows[0]
        assert r["date"] == "2026-09-04"
        assert r["code"] == "600519"
        assert r["name"] == "贵州茅台"
        assert r["market"] == "融资融券_沪证"
        # 金额单位元，量纲照抄
        assert r["rzye"] == 17034742324.0
        assert r["rzmre"] == 321312662.0
        assert r["rqye"] == 156832270.0
        assert r["rqyl"] == 117919.0
        assert r["rzrqye"] == 17191574594.0
        assert r["rzyezb"] == pytest.approx(1.02457927)
        assert r["close"] == pytest.approx(1421.0)
        assert r["pct_change"] == pytest.approx(0.87)
        # 3/5/10 日差分字段保留在 extra
        assert r["extra"]["RZMRE3D"] == 633591142
        src.close()

    def test_parse_rows_accepts_dict_payload(self):
        """基类 fetch_rows 走 parse_rows(dict) 同一归一化路径。"""
        import json

        src = EastmoneyMarginSource()
        rows = src.parse_rows(json.loads(MARGIN_JSON))
        assert rows and rows[0]["code"] == "600519"
        src.close()

    def test_parse_bad_json_raises_deprecated(self):
        src = EastmoneyMarginSource()
        with pytest.raises(SourceDeprecated):
            src.parse_margin("<!doctype html>404")
        src.close()


class TestMarginFetchOffline:
    def test_fetch_margin_days_truncates(self, monkeypatch):
        """days=N 截断最近 N 行；底层 fetch_rows 被调用且 filter 含 scode。"""
        import json

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
            captured["filters"] = list(filters)
            captured["size"] = size
            return EastmoneyMarginSource()._normalize(json.loads(MARGIN_JSON))

        monkeypatch.setattr(EastmoneyMarginSource, "fetch_rows", fake_fetch_rows)
        src = EastmoneyMarginSource()
        rows = src.fetch_margin("sh600519", days=1)
        assert captured["filters"] == ['scode="600519"']
        assert captured["size"] == 1  # size >= days
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-09-04"
        src.close()

    def test_non_margin_target_empty(self):
        """非两融标的：data 空列表 → 返回 []（不抛错）。"""
        import json

        payload = json.loads(MARGIN_JSON)
        payload["result"]["data"] = []
        src = EastmoneyMarginSource()
        rows = src.parse_margin(json.dumps(payload))
        assert rows == []
        src.close()


class TestMarginRegistry:
    def test_registered(self):
        assert "margin" in KNOWN_SOURCES
        assert "margin" in _ADAPTERS
        spec = KNOWN_SOURCES["margin"]
        assert "margin" in spec.capabilities

    def test_report_name(self):
        src = EastmoneyMarginSource()
        assert src.report == "RPTA_WEB_RZRQ_GGMX"
        src.close()
