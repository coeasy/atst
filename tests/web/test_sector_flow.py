"""板块资金流排行（sector_flow）与排行字段自动附带测试（P12 扩展）。

覆盖：资金流排序自动附带 f62/f184/f66/f72/f78/f84（P12 修复：否则行值
静默为 None 的实测陷阱）/ 非资金流排序不附带 / 罐头解析 /
session.sector_flow 与 session.sector_flow 委托。
"""

from __future__ import annotations

import json

import pytest

from tstdx.web.fundflow import EastmoneyRankSource

pytestmark = pytest.mark.unit

#: 罐头：push2 clist 行业板块资金流（2026-09-06 传媒真实行裁剪）
SECTOR_JSON = """{
  "data": {
    "total": 86,
    "diff": [
      {"f12": "BK0486", "f13": 90, "f14": "传媒", "f2": "1330.00",
       "f3": "2.32", "f62": 6174208000.0, "f184": 7.24,
       "f66": 4360000000.0, "f72": 1814000000.0,
       "f78": -1200000000.0, "f84": -2400000000.0}
    ]
  }
}"""


class TestAutoMoneyflowFields:
    def test_main_net_sort_requests_moneyflow_fields(self, monkeypatch):
        """sort=main_net：URL fields 自动包含 f62..f84（否则返回值恒 None）。"""
        captured: dict = {}

        def fake_get_json(self, url):
            captured["url"] = url
            return json.loads(SECTOR_JSON)

        monkeypatch.setattr(EastmoneyRankSource, "_get_json", fake_get_json)
        src = EastmoneyRankSource()
        rows = src.fetch_rows("industry", sort="main_net", limit=1)
        fields = captured["url"].split("fields=")[1]
        for f in ("f62", "f184", "f66", "f72", "f78", "f84"):
            assert f in fields, f"缺少资金流字段 {f}"
        # 罐头值正确归位
        assert rows[0]["main_net"] == 6174208000.0
        assert rows[0]["main_net_ratio"] == pytest.approx(7.24)
        assert rows[0]["super_large_net"] == 4360000000.0
        assert rows[0]["symbol"] == "BK0486"  # 板块行 symbol 为 BK 代码
        src.close()

    def test_change_pct_sort_does_not_add_moneyflow(self, monkeypatch):
        """非资金流排序不附带（保持个股排行轻负载）。"""
        captured: dict = {}

        def fake_get_json(self, url):
            captured["url"] = url
            return json.loads(SECTOR_JSON)

        monkeypatch.setattr(EastmoneyRankSource, "_get_json", fake_get_json)
        src = EastmoneyRankSource()
        src.fetch_rows("all_a", sort="change_pct", limit=1)
        fields = captured["url"].split("fields=")[1]
        assert "f62" not in fields
        src.close()


class TestSectorFlowWiring:
    def test_session_delegates(self, monkeypatch):
        captured: dict = {}

        def fake_fetch_rows(
            self,
            market="all_a",
            *,
            sort="change_pct",
            limit=20,
            page=1,
            ascending=False,
            extra_fields=(),
        ):
            captured.update(market=market, sort=sort, limit=limit)
            return [{"name": "传媒"}]

        monkeypatch.setattr(EastmoneyRankSource, "fetch_rows", fake_fetch_rows)
        from tstdx.web.session import WebQuoteSession

        rows = WebQuoteSession.sector_flow("concept", sort="main_net", limit=5)
        assert rows == [{"name": "传媒"}]
        assert captured == {"market": "concept", "sort": "main_net", "limit": 5}

    def test_client_capability_delegates(self, monkeypatch):
        from tstdx.client.api import Client
        from tstdx.web.session import WebQuoteSession

        def fake_sector_flow(board="industry", *, sort="main_net", limit=20, page=1):
            return [{"board": board}]

        monkeypatch.setattr(WebQuoteSession, "sector_flow", staticmethod(fake_sector_flow))
        with Client() as client:
            result = client.sector_flow("region", limit=3)
        assert result.data == [{"board": "region"}]
