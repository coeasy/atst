"""2026-10-14 批量补齐的 datacenter 报表能力。

这一组能力的共同契约，抽出来单独钉住，免得 18 个能力各自只测一遍 Happy path：

1. 报表名必须是实测过的真名（``VALID_REPORTS`` 白名单里）——猜错东财回 9501；
2. 个股维度走 ``report_code_for`` 指数守卫（``sh000001`` 撞 ``sz000001``）；
3. 稀疏报表（商誉 / 增发）查不到是**正常空集**，不是异常；
4. 能力双登记：catalog 与 Provider 注册表不许分叉。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from atst.web.base import HttpResponse
from atst.web.session import WebQuoteSession


def _payload(rows: list[dict[str, Any]], count: int | None = None) -> bytes:
    return json.dumps(
        {
            "version": "canned",
            "success": True,
            "result": {
                "pages": 1,
                "count": len(rows) if count is None else count,
                "data": rows,
            },
        }
    ).encode("utf-8")


class FakeHttp:
    """返回预置响应的假 HTTP 客户端，并记录请求 URL 供断言过滤条件。"""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> HttpResponse:
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        return None


@pytest.fixture
def session() -> Any:
    return WebQuoteSession("eastmoney")


def _run(session: Any, http: FakeHttp, fn: Any, *args: Any, **kwargs: Any) -> Any:
    with patch("atst.web._session_market._shared_http", lambda: http):
        return fn(*args, **kwargs)


class TestEveryNewReportIsARealMeasuredName:
    """报表名不许猜——这张表就是「实测过」的登记表。"""

    EXPECTED = {
        "suspension": "RPT_CUSTOM_SUSPEND_DATA_INTERFACE",
        "goodwill": "RPT_GOODWILL_STOCKDETAILS",
        "goodwill_market": "RPT_GOODWILL_MARKETSTATISTICS",
        "seo": "RPT_SEO_DETAIL",
        "equity_structure": "RPT_F10_EH_EQUITY",
        "holder_num_detail": "RPT_HOLDERNUM_DET",
        "org_seat_trades": "RPT_ORGANIZATION_TRADE_DETAILS",
        "connect_deals": "RPT_MUTUAL_DEAL_HISTORY",
        "southbound_ranks": "RPT_MUTUAL_STOCK_HOLDRANKS",
        "margin_daily": "RPTA_WEB_MARGIN_DAILYTRADE",
        "block_trade_stats": "RPT_BLOCKTRADE_STA",
        "macro_money_supply": "RPT_ECONOMY_CURRENCY_SUPPLY",
        "macro_loan": "RPT_ECONOMY_RMB_LOAN",
        "macro_retail": "RPT_ECONOMY_TOTAL_RETAIL",
        "macro_asset_invest": "RPT_ECONOMY_ASSET_INVEST",
        "macro_industrial": "RPT_ECONOMY_INDUS_GROW",
        "macro_reserve_ratio": "RPT_ECONOMY_DEPOSIT_RESERVE",
        "macro_forex_reserve": "RPT_ECONOMY_GOLD_CURRENCY",
    }

    def test_all_registered_with_the_measured_report_name(self) -> None:
        from atst.web.corporate import VALID_REPORTS

        for alias, report in self.EXPECTED.items():
            assert VALID_REPORTS.get(alias) == report, f"{alias} 的报表名不对"

    def test_every_alias_has_a_session_method_of_the_same_name(self, session: Any) -> None:
        """能力名 = 会话方法名：发现面宣布的能力必须执行得了。"""
        for alias in self.EXPECTED:
            assert hasattr(session, alias), f"WebQuoteSession 没有 {alias} 方法"

    def test_every_alias_is_declared_in_both_registries(self) -> None:
        from atst.catalog.capability import MIGRATED_CAPABILITIES
        from atst.providers import PROVIDERS

        registry: set[str] = set()
        for provider in PROVIDERS.ids():
            for channel in PROVIDERS.get(provider).channels:
                registry.update(channel.capabilities)
        for alias in self.EXPECTED:
            assert alias in MIGRATED_CAPABILITIES, f"{alias} 不在 catalog"
            assert alias in registry, f"{alias} 不在 Provider 注册表"


class TestSymbolDimensionGuardsTheIndexCodeCollision:
    """按 6 位码过滤的报表，指数必须先被挡掉。

    ``sh000001``（上证指数）与 ``sz000001``（平安银行）在这些报表里撞码，
    不挡就是把平安银行的事件安到指数头上。
    """

    @pytest.mark.parametrize(
        "capability",
        [
            "goodwill",
            "seo",
            "equity_structure",
            "holder_num_detail",
            "org_seat_trades",
            "block_trade_stats",
        ],
    )
    def test_index_symbol_returns_empty_without_any_http_call(
        self, session: Any, capability: str
    ) -> None:
        http = FakeHttp(_payload([{"SECURITY_CODE": "000001"}]))
        out = _run(session, http, getattr(session, capability), "sh000001")
        assert out == []
        assert http.calls == [], "指数必须在发包之前就被挡掉"

    def test_southbound_ranks_does_not_use_the_index_guard(self, session: Any) -> None:
        """港股通排行只收港股代码，走撞码守卫反而会把合法港股挡掉。

        这条单独钉住：它故意**不用** ``report_code_for``。
        """
        http = FakeHttp(_payload([{"SECURITY_CODE": "00700"}]))
        out = _run(session, http, session.southbound_ranks, "hk00700")
        assert len(out) == 1
        assert "SECURITY_CODE" in http.calls[-1]


class TestSparseReportsTreatEmptyAsAnAnswer:
    """商誉 / 增发是稀疏报表：查不到是正常回答，不是故障。"""

    @pytest.mark.parametrize("capability", ["goodwill", "seo"])
    def test_empty_result_is_an_empty_list_not_an_exception(
        self, session: Any, capability: str
    ) -> None:
        # 服务端对稀疏报表的正常回答：success=False + code=9201「返回数据为空」
        http = FakeHttp(
            json.dumps({"success": False, "code": 9201, "message": "返回数据为空"}).encode("utf-8")
        )
        out = _run(session, http, getattr(session, capability), "sh600519")
        assert out == []

    def test_goodwill_accepts_a_real_row(self, session: Any) -> None:
        row = {"SECURITY_CODE": "002739", "SECURITY_NAME_ABBR": "儒意电影"}
        http = FakeHttp(_payload([row]))
        out = _run(session, http, session.goodwill, "sz002739")
        assert out == [row]


class TestSuspensionRequiresADate:
    """停复牌的空 filter 会被服务端拒（9501），所以 date 是必填维度。"""

    def test_date_and_market_become_the_whole_filter(self, session: Any) -> None:
        http = FakeHttp(_payload([{"SECURITY_CODE": "301314"}]))
        _run(session, http, session.suspension, "2026-10-13")
        url = http.calls[-1]
        assert "DATETIME" in url
        assert "MARKET" in url
        assert "RPT_CUSTOM_SUSPEND_DATA_INTERFACE" in url

    def test_suspension_has_no_symbol_parameter(self, session: Any) -> None:
        """维度是交易日不是个股——签名里不该有 symbol。"""
        import inspect

        sig = inspect.signature(session.suspension)
        assert "symbol" not in sig.parameters


class TestConnectDealsCarriesBothDirections:
    """沪深港通历史成交南北双向都在一张表里，不传编号会混入反向数据。"""

    def test_mutual_type_becomes_a_filter_when_given(self, session: Any) -> None:
        http = FakeHttp(_payload([{"MUTUAL_TYPE": "001"}]))
        _run(session, http, session.connect_deals, mutual_type="001")
        assert "MUTUAL_TYPE" in http.calls[-1]

    def test_no_filter_when_mutual_type_is_omitted(self, session: Any) -> None:
        http = FakeHttp(_payload([{"MUTUAL_TYPE": "006"}]))
        _run(session, http, session.connect_deals)
        assert "MUTUAL_TYPE" not in http.calls[-1]


class TestMacroCapabilitiesAreMarketLevel:
    """宏观七张表都是市场级：不接受 symbol，按 REPORT_DATE 排序。"""

    @pytest.mark.parametrize(
        "capability",
        [
            "macro_money_supply",
            "macro_loan",
            "macro_retail",
            "macro_asset_invest",
            "macro_industrial",
            "macro_reserve_ratio",
            "macro_forex_reserve",
        ],
    )
    def test_no_symbol_and_sorted_by_report_date(self, session: Any, capability: str) -> None:
        import inspect

        sig = inspect.signature(getattr(session, capability))
        assert "symbol" not in sig.parameters, f"{capability} 不该收 symbol"

        http = FakeHttp(_payload([{"REPORT_DATE": "2026-09-01"}]))
        _run(session, http, getattr(session, capability))
        url = http.calls[-1]
        assert "REPORT_DATE" in url
        assert "SECURITY_CODE" not in url
