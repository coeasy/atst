# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P4 期权行情离线测试（ETF / 股指期权，东财 push2 后端）。

2026-09-11 抓包验证：m:10（上证50ETF期权 674）、m:11（沪深300股指期权 686）、
m:12（深证100ETF期权 452）。扩展字段 f303（1=认购/2=认沽）实测可用。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atst.web.base import HttpResponse
from atst.web.efinance_options import OPTIONS_MARKETS, EastmoneyOptionsSource
from atst.web.session import WebQuoteSession

pytestmark = [pytest.mark.unit]


# --------------------------------------------------------------------------- #
# Fake HTTP Client
# --------------------------------------------------------------------------- #
class FakeHttpClient:
    """匹配 URL 子串 → 返回预设 JSON bytes。"""

    def __init__(self, bodies: dict[str, bytes] | None = None) -> None:
        self._bodies = bodies or {}
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(url)
        for key, body in self._bodies.items():
            if key in url:
                return HttpResponse(200, body, {})
        return HttpResponse(404, b"not found", {})

    def close(self) -> None:
        pass


def _j(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# 罐头 JSON（2026-09-11 真实抓包数据）
# --------------------------------------------------------------------------- #

#: 期权合约列表（m:11 沪深300股指期权，2026-09-11 抓包）
OPTIONS_CLIST_M11 = _j(
    {
        "data": {
            "total": 686,
            "diff": [
                {
                    "f12": "IO2609-C-3900",
                    "f13": "11",
                    "f14": "沪深300购26年9月3900",
                    "f3": 0,
                    "f43": 5888,
                    "f44": 5888,
                    "f45": 5888,
                    "f46": 5888,
                    "f47": 114,
                    "f48": 673840.0,
                    "f60": 5888,
                    "f104": 0,
                    "f105": 0,
                    "f106": 5888,
                },
                {
                    "f12": "IO2609-C-4000",
                    "f13": "11",
                    "f14": "沪深300购26年9月4000",
                    "f3": 0,
                    "f43": 4776,
                    "f44": 4776,
                    "f45": 4776,
                    "f46": 4776,
                    "f47": 49,
                    "f48": 234120.0,
                    "f60": 4776,
                    "f104": 0,
                    "f105": 0,
                    "f106": 4776,
                },
            ],
        }
    }
)

#: 期权合约列表（m:10 上证50ETF期权，2026-09-11 抓包）
OPTIONS_CLIST_M10 = _j(
    {
        "data": {
            "total": 674,
            "diff": [
                {
                    "f12": "10010971",
                    "f13": "10",
                    "f14": "50ETF购9月2850",
                    "f3": 0,
                    "f43": 0,
                    "f44": 0,
                    "f45": 0,
                    "f46": 0,
                    "f47": 0,
                    "f48": 0.0,
                    "f60": 0,
                    "f104": 0,
                    "f105": 0,
                    "f106": 0,
                }
            ],
        }
    }
)

#: 期权合约列表（m:12 深证100ETF期权，2026-09-11 抓包）
OPTIONS_CLIST_M12 = _j(
    {
        "data": {
            "total": 452,
            "diff": [
                {
                    "f12": "90007051",
                    "f13": "12",
                    "f14": "深证100ETF购9月3100",
                    "f3": 0,
                    "f43": 0,
                    "f44": 0,
                    "f45": 0,
                    "f46": 0,
                    "f47": 0,
                    "f48": 0.0,
                    "f60": 0,
                    "f104": 0,
                    "f105": 0,
                    "f106": 0,
                }
            ],
        }
    }
)

#: 期权快照（认购，f303=1）
OPTIONS_SNAPSHOT_CALL = _j(
    {
        "data": {
            "f12": "IO2609-C-3900",
            "f13": "11",
            "f14": "沪深300购26年9月3900",
            "f43": 5888,
            "f44": 5888,
            "f45": 5888,
            "f46": 5888,
            "f47": 5888,
            "f48": 673840.0,
            "f57": "IO2609-C-3900",
            "f58": "沪深300购26年9月3900",
            "f60": 5888,
            "f301": 5888,
            "f302": 58880,
            "f303": 1,
        }
    }
)

#: 期权快照（认沽，f303=2）
OPTIONS_SNAPSHOT_PUT = _j(
    {
        "data": {
            "f12": "10010971",
            "f13": "10",
            "f14": "50ETF购9月2850",
            "f43": 1798,
            "f44": 1798,
            "f45": 1798,
            "f46": 1798,
            "f47": 1798,
            "f48": 0.0,
            "f57": "10010971",
            "f58": "50ETF购9月2850",
            "f60": 1798,
            "f301": 1798,
            "f302": 17980,
            "f303": 2,
        }
    }
)

#: 期权分时（m:11 可用）
OPTIONS_TRENDS_OK = _j(
    {
        "data": {
            "trends": [
                "2026-09-11 09:30,642.2,642.2,646.0,642.2,0,0.0,642.20",
                "2026-09-11 09:31,642.2,643.0,643.0,642.2,100,64300.0,643.00",
            ]
        }
    }
)

#: 期权分时（空数据）
OPTIONS_TRENDS_EMPTY = _j({"data": {"trends": []}})


# --------------------------------------------------------------------------- #
# 期权合约列表测试
# --------------------------------------------------------------------------- #
class TestOptionsContractList:
    def test_list_m11(self) -> None:
        """按市场段 m:11 筛选沪深300股指期权。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"clist/get": OPTIONS_CLIST_M11}))
        rows = src.fetch_contract_list(market="11")
        assert len(rows) == 2
        assert rows[0]["quote_id"] == "11.IO2609-C-3900"
        assert rows[0]["name"] == "沪深300购26年9月3900"
        assert rows[0]["market"] == "11"
        assert rows[0]["price"] == pytest.approx(58.88)  # 5888 / 100

    def test_list_m10(self) -> None:
        """按市场段 m:10 筛选上证50ETF期权。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"clist/get": OPTIONS_CLIST_M10}))
        rows = src.fetch_contract_list(market="10")
        assert len(rows) == 1
        assert rows[0]["quote_id"] == "10.10010971"
        assert rows[0]["name"] == "50ETF购9月2850"

    def test_list_m12(self) -> None:
        """按市场段 m:12 筛选深证100ETF期权。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"clist/get": OPTIONS_CLIST_M12}))
        rows = src.fetch_contract_list(market="12")
        assert len(rows) == 1
        assert rows[0]["quote_id"] == "12.90007051"

    def test_list_all_markets(self) -> None:
        """空 market 参数查询全部市场。"""
        client = FakeHttpClient({"clist/get": OPTIONS_CLIST_M11})
        src = EastmoneyOptionsSource(client=client)
        rows = src.fetch_contract_list()
        assert len(rows) >= 1
        # 验证 URL 包含全部市场段
        url = client.calls[0]
        assert "m:10" in url
        assert "m:11" in url
        assert "m:12" in url

    def test_list_size_clamped(self) -> None:
        """size 参数被 clamped 到 [1, 500]。"""
        client = FakeHttpClient({"clist/get": OPTIONS_CLIST_M11})
        src = EastmoneyOptionsSource(client=client)
        src.fetch_contract_list(market="11", size=9999)
        assert "pz=500" in client.calls[0]

    def test_list_page(self) -> None:
        """page 参数传递到 URL。"""
        client = FakeHttpClient({"clist/get": OPTIONS_CLIST_M11})
        src = EastmoneyOptionsSource(client=client)
        src.fetch_contract_list(market="11", page=3)
        assert "pn=3" in client.calls[0]

    def test_list_change_pct(self) -> None:
        """涨跌幅字段除以 100 后正确。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"clist/get": OPTIONS_CLIST_M11}))
        rows = src.fetch_contract_list(market="11")
        assert rows[0]["change_pct"] == pytest.approx(0.0)

    def test_list_volume(self) -> None:
        """成交量字段为整数。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"clist/get": OPTIONS_CLIST_M11}))
        rows = src.fetch_contract_list(market="11")
        assert isinstance(rows[0]["volume"], int)
        assert rows[0]["volume"] == 114


# --------------------------------------------------------------------------- #
# 期权快照测试
# --------------------------------------------------------------------------- #
class TestOptionsSnapshot:
    def test_snapshot_call(self) -> None:
        """认购期权快照（f303=1）。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"stock/get": OPTIONS_SNAPSHOT_CALL}))
        snap = src.fetch_snapshot("11.IO2609-C-3900")
        assert snap["quote_id"] == "11.IO2609-C-3900"
        assert snap["name"] == "沪深300购26年9月3900"
        assert snap["price"] == pytest.approx(58.88)  # 5888 / 100
        assert snap["option_type"] == 1
        assert snap["option_type_label"] == "认购"
        assert snap["option_price_raw"] == 5888.0

    def test_snapshot_put(self) -> None:
        """认沽期权快照（f303=2）。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"stock/get": OPTIONS_SNAPSHOT_PUT}))
        snap = src.fetch_snapshot("10.10010971")
        assert snap["option_type"] == 2
        assert snap["option_type_label"] == "认沽"

    def test_snapshot_auto_prefix(self) -> None:
        """仅传合约代码时自动补 11. 前缀。"""
        client = FakeHttpClient({"stock/get": OPTIONS_SNAPSHOT_CALL})
        src = EastmoneyOptionsSource(client=client)
        src.fetch_snapshot("IO2609-C-3900")
        assert "secid=11.IO2609-C-3900" in client.calls[0]

    def test_snapshot_fields(self) -> None:
        """快照包含扩展字段 f303-f310。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"stock/get": OPTIONS_SNAPSHOT_CALL}))
        snap = src.fetch_snapshot("11.IO2609-C-3900")
        assert "f303" in snap
        assert "f304" in snap
        assert "f310" in snap
        assert snap["f303"] == "1"

    def test_snapshot_empty_raises(self) -> None:
        """空快照抛出 SourceDeprecated。"""
        from atst.errors import SourceDeprecated

        src = EastmoneyOptionsSource(client=FakeHttpClient({"stock/get": _j({"data": {}})}))
        with pytest.raises(SourceDeprecated):
            src.fetch_snapshot("11.IO2609-C-3900")


# --------------------------------------------------------------------------- #
# 期权分时测试
# --------------------------------------------------------------------------- #
class TestOptionsTrends:
    def test_trends_success(self) -> None:
        """分时数据正常返回。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"trends2/get": OPTIONS_TRENDS_OK}))
        rows = src.fetch_trends("11.IO2609-C-3900")
        assert len(rows) == 2
        assert rows[0]["datetime"] == "2026-09-11 09:30"
        assert rows[0]["open"] == pytest.approx(642.2)
        assert rows[0]["close"] == pytest.approx(642.2)
        assert rows[0]["avg_price"] == pytest.approx(642.20)

    def test_trends_empty(self) -> None:
        """分时返回空列表。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"trends2/get": OPTIONS_TRENDS_EMPTY}))
        rows = src.fetch_trends("10.10010971")
        assert rows == []

    def test_trends_ndays(self) -> None:
        """ndays 参数传递到 URL。"""
        client = FakeHttpClient({"trends2/get": OPTIONS_TRENDS_EMPTY})
        src = EastmoneyOptionsSource(client=client)
        src.fetch_trends("11.IO2609-C-3900", ndays=3)
        assert "ndays=3" in client.calls[0]

    def test_trends_auto_prefix(self) -> None:
        """仅传合约代码时自动补 11. 前缀。"""
        client = FakeHttpClient({"trends2/get": OPTIONS_TRENDS_EMPTY})
        src = EastmoneyOptionsSource(client=client)
        src.fetch_trends("IO2609-C-3900")
        assert "secid=11.IO2609-C-3900" in client.calls[0]

    def test_trends_volume_int(self) -> None:
        """分时成交量为整数。"""
        src = EastmoneyOptionsSource(client=FakeHttpClient({"trends2/get": OPTIONS_TRENDS_OK}))
        rows = src.fetch_trends("11.IO2609-C-3900")
        assert isinstance(rows[0]["volume"], int)


# --------------------------------------------------------------------------- #
# 期权市场段常量测试
# --------------------------------------------------------------------------- #
class TestOptionsMarkets:
    def test_three_markets(self) -> None:
        """三个期权市场段已注册。"""
        assert len(OPTIONS_MARKETS) == 3
        assert OPTIONS_MARKETS["10"] == "上证50ETF期权"
        assert OPTIONS_MARKETS["11"] == "沪深300股指期权"
        assert OPTIONS_MARKETS["12"] == "深证100ETF期权"


# --------------------------------------------------------------------------- #
# 门面 hasattr 全链测试
# --------------------------------------------------------------------------- #
class TestP4FacadeIntegration:
    def test_derivative_mixin_has_options(self) -> None:
        """DerivativeSessionMixin 包含期权方法。"""
        from atst.web._session_efinance import DerivativeSessionMixin

        for name in ["options_list", "options_snapshot", "options_trends"]:
            assert hasattr(DerivativeSessionMixin, name), f"缺少方法: {name}"

    def test_web_session_has_options(self) -> None:
        """WebQuoteSession 继承链包含期权方法。"""
        from atst.web.session import WebQuoteSession

        for name in ["options_list", "options_snapshot", "options_trends"]:
            assert hasattr(WebQuoteSession, name), f"缺少方法: {name}"

    def test_unified_api_has_options(self) -> None:
        """WebQuoteSession 包含期权方法。"""

        for name in ["options_list", "options_snapshot", "options_trends"]:
            assert hasattr(WebQuoteSession, name), f"缺少方法: {name}"
