# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P1 数据源离线测试（ESG 评级 / 筹码分布）——37 个测试。

使用 FakeHttpClient 模拟 HTTP 响应，零真实 HTTP 调用。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atst.web.base import HttpResponse
from atst.web.chip import EastmoneyChipDistributionSource
from atst.web.esg import (
    SinaEsgHistorySource,
    SinaEsgHzSource,
    SinaEsgMsciSource,
    SinaEsgStockInfoSource,
)


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
ESG_STOCK_INFO = {
    "result": {
        "status": {"code": 0},
        "data": {
            "code": 200000,
            "info": [
                {
                    "agency": "agency13",
                    "agency_name": "MSCI",
                    "esg_score": "A",
                    "esg_level": "A",
                    "esg_level_alias": "",
                    "esg_dt": "2026-06-22",
                    "detail": [
                        {"name": "环境总评", "date": "2026-06-22", "score": "7.48", "detail": []},
                        {
                            "name": "社会责任总评",
                            "date": "2026-06-22",
                            "score": "6.01",
                            "detail": [],
                        },
                        {"name": "治理总评", "date": "2026-06-22", "score": "4.4", "detail": []},
                    ],
                },
                {
                    "agency": "agency16",
                    "agency_name": "标普",
                    "esg_score": "30+",
                    "esg_level": "30+",
                    "esg_level_alias": "",
                    "esg_dt": "2025-02-03",
                    "detail": [],
                },
                {
                    "agency": "agency14",
                    "agency_name": "中证指数",
                    "esg_score": "AAA",
                    "esg_level": "AAA",
                    "esg_level_alias": "",
                    "esg_dt": "2026-08-21",
                    "detail": [
                        {"name": "环境总评", "date": "2026-08-21", "score": "0.9", "detail": []},
                        {"name": "社会责任总评", "date": "2026-08-21", "score": "1", "detail": []},
                        {"name": "治理总评", "date": "2026-08-21", "score": "0.9", "detail": []},
                    ],
                },
            ],
        },
    }
}

ESG_HISTORY = {
    "result": {
        "status": {"code": 0},
        "data": [
            {
                "agency": "agency6",
                "agency_name": "伦交所",
                "history": {"2025Q2": "61.9", "2025Q3": "61.4", "2025Q4": "", "2026Q1": "57.6"},
                "keyList": None,
                "type": "num",
            },
            {
                "agency": "agency12",
                "agency_name": "华证指数",
                "history": {"2025Q3": "AAA", "2025Q4": "AAA", "2026Q1": "AAA", "2026Q2": "AAA"},
                "keyList": ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C"],
                "type": "string",
            },
            {
                "agency": "agency10",
                "agency_name": "秩鼎",
                "history": {
                    "2025Q4": "86.25",
                    "2026Q1": "86.97",
                    "2026Q2": "88.4",
                    "2026Q3": "84.51",
                },
                "keyList": ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C"],
                "type": "num",
            },
        ],
    }
}

ESG_MSCI = {
    "result": {
        "status": {"code": 0},
        "data": {
            "total": "5216",
            "data": [
                {
                    "symbol": "000001.SZ",
                    "quarter_date": "2026-07-08",
                    "market": "CN",
                    "esg_rating": "AAA",
                    "env_score": "6.3",
                    "social_score": "6.0",
                    "governance_score": "5.4",
                },
                {
                    "symbol": "000513.SZ",
                    "quarter_date": "2025-04-08",
                    "market": "CN",
                    "esg_rating": "AAA",
                    "env_score": "7.0",
                    "social_score": "6.4",
                    "governance_score": "6.3",
                },
                {
                    "symbol": "00066.HK",
                    "quarter_date": "2026-07-01",
                    "market": "HK",
                    "esg_rating": "AAA",
                    "env_score": "7.4",
                    "social_score": "5.4",
                    "governance_score": "6.1",
                },
                {
                    "symbol": "600519.SH",
                    "quarter_date": "2026-06-22",
                    "market": "CN",
                    "esg_rating": "A",
                    "env_score": "7.48",
                    "social_score": "6.01",
                    "governance_score": "4.4",
                },
                {
                    "symbol": "601318.SH",
                    "quarter_date": "2026-06-22",
                    "market": "CN",
                    "esg_rating": "BBB",
                    "env_score": "4.2",
                    "social_score": "5.8",
                    "governance_score": "5.0",
                },
            ],
        },
    }
}

ESG_HZ = {
    "result": {
        "status": {"code": 0},
        "data": {
            "total": "6355",
            "data": [
                {
                    "date": "2026-04-30",
                    "symbol": "603605.SH",
                    "market": "cn",
                    "name": "珠利雅",
                    "esg_score": "100",
                    "esg_score_grade": "AAA",
                    "e_score": "89.46",
                    "e_score_grade": "A",
                    "s_score": "88.5",
                    "s_score_grade": "A",
                    "g_score": "92.17",
                    "g_score_grade": "AA",
                },
                {
                    "date": "2026-04-30",
                    "symbol": "600522.SH",
                    "market": "cn",
                    "name": "中天科技",
                    "esg_score": "100",
                    "esg_score_grade": "AAA",
                    "e_score": "86.25",
                    "e_score_grade": "A",
                    "s_score": "93.03",
                    "s_score_grade": "AA",
                    "g_score": "92.78",
                    "g_score_grade": "AA",
                },
                {
                    "date": "2026-04-30",
                    "symbol": "300059.SZ",
                    "market": "cn",
                    "name": "东方财富",
                    "esg_score": "100",
                    "esg_score_grade": "AAA",
                    "e_score": "80.5",
                    "e_score_grade": "BBB",
                    "s_score": "94.36",
                    "s_score_grade": "AA",
                    "g_score": "89.78",
                    "g_score_grade": "A",
                },
                {
                    "date": "2026-04-30",
                    "symbol": "002709.SZ",
                    "market": "cn",
                    "name": "天赐材料",
                    "esg_score": "85",
                    "esg_score_grade": "AA",
                    "e_score": "75.2",
                    "e_score_grade": "B",
                    "s_score": "88.1",
                    "s_score_grade": "AA",
                    "g_score": "89.0",
                    "g_score_grade": "A",
                },
            ],
        },
    }
}

CHIP_DISTRIBUTION = {
    "rc": 0,
    "rt": 21,
    "svr": 180606397,
    "lt": 1,
    "full": 0,
    "dlmkts": "",
    "dsc": "0",
    "data": {
        "code": "600519",
        "market": 1,
        "name": "贵州茅台",
        "tradePeriods": {
            "pre": {"b": 202609100915, "e": 202609100930},
            "after": {"b": 202609101500, "e": 202609101530},
            "periods": [
                {"b": 202609100930, "e": 202609101130},
                {"b": 202609101300, "e": 202609101500},
            ],
        },
        "klines": [
            "2026-09-08,-132647664.0,0.0,132647664.0,-102072928.0,-30574736.0,1280.00,0.00,-132647664.0,0.00,132647664.0,-102072928.0,-30574736.0,4231000000.00,0.10",
            "2026-09-09,-277123472.0,-68489.0,277191968.0,-138407152.0,-138716320.0,1282.00,0.16,-277123472.0,-68489.0,277191968.0,-138407152.0,-138716320.0,5890000000.00,0.14",
            "2026-09-10,-374051552.0,-75805.0,374127360.0,-149640400.0,-224411152.0,1285.13,0.24,-374051552.0,-75805.0,374127360.0,-149640400.0,-224411152.0,6500000000.00,0.16",
        ],
    },
}


# --------------------------------------------------------------------------- #
# ESG 个股评级详情测试
# --------------------------------------------------------------------------- #
class TestEsgStockInfo:
    @pytest.fixture()
    def src(self) -> SinaEsgStockInfoSource:
        client = FakeHttpClient({"EsgService.getEsgStockInfo": ESG_STOCK_INFO})
        s = SinaEsgStockInfoSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_build_url_contains_symbol(self, src: SinaEsgStockInfoSource) -> None:
        url = src.build_url(["600519"])
        assert "getEsgStockInfo" in url
        assert "sh600519" in url

    def test_parse_agencies(self, src: SinaEsgStockInfoSource) -> None:
        result = src.fetch_stock_info("600519")
        assert result is not None
        agencies = result["agencies"]
        assert len(agencies) == 3

    def test_msci_agency_fields(self, src: SinaEsgStockInfoSource) -> None:
        result = src.fetch_stock_info("600519")
        msci = [a for a in result["agencies"] if a["agency_name"] == "MSCI"][0]
        assert msci["esg_score"] == "A"
        assert msci["esg_dt"] == "2026-06-22"
        assert msci["e_score"] == 7.48
        assert msci["s_score"] == 6.01
        assert msci["g_score"] == 4.4

    def test_empty_detail_agency(self, src: SinaEsgStockInfoSource) -> None:
        result = src.fetch_stock_info("600519")
        sp = [a for a in result["agencies"] if a["agency_name"] == "标普"][0]
        assert sp["esg_score"] == "30+"
        assert sp["e_score"] is None  # empty detail

    def test_none_payload(self) -> None:
        client = FakeHttpClient(
            {"EsgService.getEsgStockInfo": {"result": {"status": {"code": 0}, "data": {}}}}
        )
        s = SinaEsgStockInfoSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        assert s.fetch_stock_info("600519") is None


# --------------------------------------------------------------------------- #
# ESG 历史测试
# --------------------------------------------------------------------------- #
class TestEsgHistory:
    @pytest.fixture()
    def src(self) -> SinaEsgHistorySource:
        client = FakeHttpClient({"EsgService.getEsgStockHistory": ESG_HISTORY})
        s = SinaEsgHistorySource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_build_url(self, src: SinaEsgHistorySource) -> None:
        url = src.build_url(["600519"])
        assert "getEsgStockHistory" in url
        assert "sh600519" in url

    def test_parse_agencies(self, src: SinaEsgHistorySource) -> None:
        result = src.fetch_history("600519")
        assert result is not None
        assert len(result["agencies"]) == 3

    def test_history_fields(self, src: SinaEsgHistorySource) -> None:
        result = src.fetch_history("600519")
        lseg = [a for a in result["agencies"] if a["agency_name"] == "伦交所"][0]
        assert lseg["type"] == "num"
        assert "2025Q2" in lseg["history"]
        assert lseg["history"]["2025Q2"] == "61.9"

    def test_string_type_history(self, src: SinaEsgHistorySource) -> None:
        result = src.fetch_history("600519")
        hz = [a for a in result["agencies"] if a["agency_name"] == "华证指数"][0]
        assert hz["type"] == "string"
        assert hz["history"]["2026Q2"] == "AAA"


# --------------------------------------------------------------------------- #
# MSCI 全市场 ESG 测试
# --------------------------------------------------------------------------- #
class TestEsgMsci:
    @pytest.fixture()
    def src(self) -> SinaEsgMsciSource:
        client = FakeHttpClient({"EsgService.getMsciEsgStocks": ESG_MSCI})
        s = SinaEsgMsciSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_total_count(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings()
        assert result["total"] == 5216

    def test_filter_market_cn(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings(market="CN")
        assert all(r["market"] == "CN" for r in result["ratings"])
        assert len(result["ratings"]) == 4

    def test_filter_market_hk(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings(market="HK")
        assert len(result["ratings"]) == 1
        assert result["ratings"][0]["symbol"] == "00066.HK"

    def test_filter_rating(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings(rating="AAA")
        assert all(r["esg_rating"] == "AAA" for r in result["ratings"])

    def test_sort_by_score(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings(sort_column="env_score", sort_order="desc")
        scores = [r["env_score"] for r in result["ratings"] if r["env_score"] is not None]
        assert scores == sorted(scores, reverse=True)

    def test_env_score_numeric(self, src: SinaEsgMsciSource) -> None:
        result = src.fetch_ratings()
        m = [r for r in result["ratings"] if r["symbol"] == "600519.SH"][0]
        assert m["env_score"] == 7.48
        assert m["social_score"] == 6.01


# --------------------------------------------------------------------------- #
# 华证全市场 ESG 测试
# --------------------------------------------------------------------------- #
class TestEsgHz:
    @pytest.fixture()
    def src(self) -> SinaEsgHzSource:
        client = FakeHttpClient({"EsgService.getHzEsgStocks": ESG_HZ})
        s = SinaEsgHzSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_total_count(self, src: SinaEsgHzSource) -> None:
        result = src.fetch_ratings()
        assert result["total"] == 6355

    def test_all_cn_market(self, src: SinaEsgHzSource) -> None:
        result = src.fetch_ratings()
        assert all(r["market"] == "cn" for r in result["ratings"])

    def test_filter_grade(self, src: SinaEsgHzSource) -> None:
        result = src.fetch_ratings(grade="AAA")
        assert all(r["esg_score_grade"] == "AAA" for r in result["ratings"])
        assert len(result["ratings"]) == 3

    def test_sort_by_esg_score(self, src: SinaEsgHzSource) -> None:
        result = src.fetch_ratings(sort_column="esg_score", sort_order="desc")
        scores = [r["esg_score"] for r in result["ratings"] if r["esg_score"] is not None]
        assert scores == sorted(scores, reverse=True)

    def test_e_s_g_fields(self, src: SinaEsgHzSource) -> None:
        result = src.fetch_ratings()
        r = [r for r in result["ratings"] if r["symbol"] == "603605.SH"][0]
        assert r["e_score"] == 89.46
        assert r["s_score"] == 88.5
        assert r["g_score"] == 92.17
        assert r["name"] == "珠利雅"


# --------------------------------------------------------------------------- #
# 筹码分布测试
# --------------------------------------------------------------------------- #
class TestChipDistribution:
    @pytest.fixture()
    def src(self) -> EastmoneyChipDistributionSource:
        client = FakeHttpClient({"fflow/kline/get": CHIP_DISTRIBUTION})
        s = EastmoneyChipDistributionSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        return s

    def test_build_url_contains_secid(self, src: EastmoneyChipDistributionSource) -> None:
        url = src.build_url(["600519"])
        assert "fflow/kline/get" in url
        assert "1.600519" in url
        assert "klt=101" in url

    def test_parse_basic_fields(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        assert result is not None
        assert result["symbol"] == "600519"
        assert result["name"] == "贵州茅台"

    def test_daily_count(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        assert len(result["daily"]) == 3

    def test_daily_fields(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        d = result["daily"][-1]
        assert d["date"] == "2026-09-10"
        assert d["main_net"] == -374051552.0
        assert d["amount"] == 6500000000.0

    def test_accumulation_ratio_calculation(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        # 最近一天: main_net=-374051552, large_net=374127360, amount=6500000000
        # accumulation = (main + large) / amount * 100
        expected = round((-374051552.0 + 374127360.0) / 6500000000.0 * 100, 4)
        assert result["daily"][-1]["accumulation_ratio"] == expected

    def test_total_accumulation_ratio(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        assert result["accumulation_ratio"] is not None
        # 所有天累计：(sum(main_net) + sum(large_net)) / sum(amount) * 100
        total_main = sum(d["main_net"] for d in result["daily"])
        total_large = sum(d["large_net"] for d in result["daily"])
        total_amount = sum(d["amount"] for d in result["daily"])
        expected = round((total_main + total_large) / total_amount * 100, 4)
        assert result["accumulation_ratio"] == expected

    def test_concentration_trend(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        assert result["concentration_trend"] in ("accumulating", "dispersing", "neutral")

    def test_period_string(self, src: EastmoneyChipDistributionSource) -> None:
        result = src.fetch_chip_distribution("600519", days=3)
        assert result["period"] == "2026-09-08~2026-09-10"

    def test_none_on_error(self) -> None:
        client = FakeHttpClient({"fflow/kline/get": {"rc": 102, "data": None}})
        s = EastmoneyChipDistributionSource(client=client)
        s.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
        assert s.fetch_chip_distribution("600519") is None

    def test_shenzhen_symbol(self, src: EastmoneyChipDistributionSource) -> None:
        url = src.build_url(["000001"])
        assert "0.000001" in url
