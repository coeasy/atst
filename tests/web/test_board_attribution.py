# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""所属板块（``board_attribution``）测试。

罐头响应取自 2026-10-14 真机抓包（东财 datacenter
``RPT_F10_CORETHEME_BOARDTYPE``），全部离线不发起真实 HTTP；live 冒烟单独标注
``network``，网络受限自动跳过。

覆盖四条口径：

* 个股 → 所属板块（``symbol``，``report_code_for`` 指数守卫挡掉 ``sh000001``
  与 ``sz000001`` 的撞码）；
* 板块 → 成分（``board_code`` 收 BK 码与 F10 数字码两种形状）；
* 板块中文名（``board_name``）与三个维度的互斥优先级；
* 能力双登记对账——漏一处 ``Client()`` 构造即抛 ``CapabilityAuditError``。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from atst.catalog.capability import MIGRATED_BINDINGS, MIGRATED_CAPABILITY_STATUS
from atst.web.base import HttpResponse
from atst.web.corporate import VALID_REPORTS, EastmoneyDataCenterSource, report_code_for
from atst.web.session import WebQuoteSession

pytestmark = pytest.mark.unit


class FakeHttp:
    """返回预置响应的假 HTTP 客户端，并记录请求 URL 供断言过滤条件。"""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


def _noop_limiter(src: EastmoneyDataCenterSource) -> EastmoneyDataCenterSource:
    src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
    return src


def _payload(rows: list[dict[str, Any]], *, count: int | None = None) -> bytes:
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


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "SECUCODE": "600519.SH",
        "SECURITY_CODE": "600519",
        "SECURITY_NAME_ABBR": "贵州茅台",
        "BOARD_CODE": "438",
        "BOARD_NAME": "食品饮料",
        "SELECTED_BOARD_REASON": None,
        "IS_PRECISE": "0",
        "BOARD_RANK": 1,
        "BOARD_YIELD": None,
        "NEW_BOARD_CODE": "BK0438",
        "DERIVE_BOARD_CODE": "BI0438",
        "BOARD_TYPE": "行业",
        "BOARD_LEVEL": "1",
    }
    base.update(over)
    return base


#: 贵州茅台前 3 个板块（2026-10-14 实测，按 BOARD_RANK 升序）
MAOTAI_ROWS = [
    _row(),
    _row(
        BOARD_CODE="1277",
        BOARD_NAME="白酒Ⅱ",
        BOARD_RANK=2,
        IS_PRECISE=None,
        NEW_BOARD_CODE="BK1277",
        DERIVE_BOARD_CODE="BI1277",
        BOARD_LEVEL="2",
    ),
    _row(
        BOARD_CODE="1575",
        BOARD_NAME="白酒Ⅲ",
        BOARD_RANK=3,
        IS_PRECISE=None,
        NEW_BOARD_CODE="BK1575",
        DERIVE_BOARD_CODE="BI1575",
        BOARD_LEVEL="3",
    ),
]

#: 「白酒」板块成分截选（2026-10-14 实测）
BAIJIU_MEMBERS = [
    _row(
        SECUCODE="000568.SZ",
        SECURITY_CODE="000568",
        SECURITY_NAME_ABBR="泸州老窖",
        BOARD_CODE="896",
        BOARD_NAME="白酒",
        BOARD_RANK=2,
        NEW_BOARD_CODE="BK0896",
        DERIVE_BOARD_CODE="BI0896",
    ),
    _row(
        SECUCODE="000596.SZ",
        SECURITY_CODE="000596",
        SECURITY_NAME_ABBR="古井贡酒",
        BOARD_CODE="896",
        BOARD_NAME="白酒",
        BOARD_RANK=3,
        NEW_BOARD_CODE="BK0896",
        DERIVE_BOARD_CODE="BI0896",
    ),
]


def _session(body: bytes) -> Any:
    """把 ``_shared_http`` 换成返回罐头响应的 fake（门面方法在模块内查它）。"""
    from atst.web import _session_market as mod

    return patch.object(mod, "_shared_http", lambda: FakeHttp(body))


def _session_with(client: FakeHttp) -> Any:
    """同 :func:`_session`，但复用调用方的 fake——用于断言「一次 HTTP 都没发」。"""
    from atst.web import _session_market as mod

    return patch.object(mod, "_shared_http", lambda: client)


# --------------------------------------------------------------------------- #
# 报表名登记
# --------------------------------------------------------------------------- #


def test_report_name_registered() -> None:
    """报表名必须写在 VALID_REPORTS 里，且指向实测存在的 RPT_ 名。"""
    assert VALID_REPORTS["board_attribution"] == "RPT_F10_CORETHEME_BOARDTYPE"


def test_source_resolves_report_key() -> None:
    s = EastmoneyDataCenterSource(report="board_attribution", client=FakeHttp(b"{}"))
    assert s.report == "RPT_F10_CORETHEME_BOARDTYPE"


# --------------------------------------------------------------------------- #
# 个股 → 所属板块
# --------------------------------------------------------------------------- #


class TestStockToBoards:
    def test_returns_all_boards_of_one_stock(self) -> None:
        with _session(_payload(MAOTAI_ROWS)):
            rows = WebQuoteSession("eastmoney").board_attribution("sh600519")
        assert [r["BOARD_NAME"] for r in rows] == ["食品饮料", "白酒Ⅱ", "白酒Ⅲ"]
        assert [r["BOARD_RANK"] for r in rows] == [1, 2, 3]

    def test_filter_carries_six_digit_code(self) -> None:
        """带前缀与裸码都必须解析成同一个 6 位 SECURITY_CODE filter。"""
        client = FakeHttp(_payload(MAOTAI_ROWS))
        s = _noop_limiter(EastmoneyDataCenterSource(report="board_attribution", client=client))
        s.fetch_rows(filters=['SECURITY_CODE="600519"'], size=10)
        assert "SECURITY_CODE%3D%22600519%22" in client.calls[-1]

    def test_bare_code_and_prefixed_code_agree(self) -> None:
        from atst.web.corporate import report_code_for as rcf

        assert rcf("sh600519") == rcf("600519") == "600519"

    def test_default_sort_is_board_rank_ascending(self) -> None:
        """个股 → 板块时 rank 1 是主营板块，必须排在最前。"""
        with _session(_payload(MAOTAI_ROWS)):
            rows = WebQuoteSession("eastmoney").board_attribution("sh600519")
        assert rows[0]["BOARD_RANK"] == min(r["BOARD_RANK"] for r in rows)


# --------------------------------------------------------------------------- #
# 指数撞码守卫
# --------------------------------------------------------------------------- #


class TestIndexGuard:
    @pytest.mark.parametrize(
        "symbol", ["sh000001", "sh000300", "sh000688", "sz399001", "sz399006", "sz399905"]
    )
    def test_index_symbols_have_no_report_code(self, symbol: str) -> None:
        assert report_code_for(symbol) is None

    @pytest.mark.parametrize("symbol", ["sh000001", "sz399001"])
    def test_index_symbol_returns_empty(self, symbol: str) -> None:
        with _session(_payload(MAOTAI_ROWS)):
            assert WebQuoteSession("eastmoney").board_attribution(symbol) == []

    def test_index_guard_short_circuits_without_http(self) -> None:
        """指数必须在发包前就被挡掉——一次 HTTP 都不该发生。"""
        client = FakeHttp(_payload(MAOTAI_ROWS))
        with _session_with(client):
            assert WebQuoteSession("eastmoney").board_attribution("sh000001") == []
        assert client.calls == []


# --------------------------------------------------------------------------- #
# 板块 → 成分
# --------------------------------------------------------------------------- #


class TestBoardToMembers:
    def test_bk_code_filters_new_board_code(self) -> None:
        with _session(_payload(BAIJIU_MEMBERS)):
            rows = WebQuoteSession("eastmoney").board_attribution(board_code="BK0896")
        assert [r["SECURITY_CODE"] for r in rows] == ["000568", "000596"]

    def test_numeric_board_code_filters_board_code(self) -> None:
        with _session(_payload(BAIJIU_MEMBERS)):
            rows = WebQuoteSession("eastmoney").board_attribution(board_code="896")
        assert [r["BOARD_CODE"] for r in rows] == ["896", "896"]

    def test_bk_prefix_is_case_insensitive(self) -> None:
        with _session(_payload(BAIJIU_MEMBERS)):
            rows = WebQuoteSession("eastmoney").board_attribution(board_code="bk0896")
        assert rows

    def test_board_name_filters_board_name(self) -> None:
        with _session(_payload(BAIJIU_MEMBERS)):
            rows = WebQuoteSession("eastmoney").board_attribution(board_name="白酒")
        assert {r["BOARD_NAME"] for r in rows} == {"白酒"}


# --------------------------------------------------------------------------- #
# 互斥优先级与分页
# --------------------------------------------------------------------------- #


class TestFilterPrecedenceAndPaging:
    def test_symbol_wins_over_board_code_and_name(self) -> None:
        """三个维度互斥：symbol 给了就不该再拼第二道 filter。"""
        client = FakeHttp(_payload(MAOTAI_ROWS))
        with _session_with(client):
            WebQuoteSession("eastmoney").board_attribution(
                "sh600519", board_code="BK0896", board_name="白酒"
            )
        url = client.calls[-1]
        assert "SECURITY_CODE" in url
        assert "NEW_BOARD_CODE" not in url
        assert "BOARD_NAME" not in url

    def test_board_code_wins_over_board_name(self) -> None:
        client = FakeHttp(_payload(BAIJIU_MEMBERS))
        with _session_with(client):
            WebQuoteSession("eastmoney").board_attribution(board_code="BK0896", board_name="白酒")
        url = client.calls[-1]
        assert "NEW_BOARD_CODE" in url
        assert "BOARD_NAME" not in url

    def test_all_pages_stops_on_short_page(self) -> None:
        rows = [
            _row(SECURITY_CODE=f"{i:06d}", SECUCODE=f"{i:06d}.SZ", SECURITY_NAME_ABBR=f"标的{i}")
            for i in range(3)
        ]
        client = FakeHttp(_payload(rows, count=3))
        s = _noop_limiter(EastmoneyDataCenterSource(report="board_attribution", client=client))
        out = s.fetch_rows(size=500, all_pages=True)
        assert len(out) == 3
        assert len(client.calls) == 1  # 短页即止，不空跑第二页

    def test_page_size_capped_at_500(self) -> None:
        client = FakeHttp(_payload(MAOTAI_ROWS))
        s = _noop_limiter(EastmoneyDataCenterSource(report="board_attribution", client=client))
        s.fetch_rows(size=9999)
        assert "pageSize=500" in client.calls[-1]

    def test_empty_result_is_not_an_error(self) -> None:
        """没归属过任何板块的标的：9201 是正常回答，不当故障抛。"""
        body = json.dumps({"success": False, "message": "返回数据为空", "code": 9201}).encode(
            "utf-8"
        )
        s = _noop_limiter(
            EastmoneyDataCenterSource(report="board_attribution", client=FakeHttp(body))
        )
        assert s.fetch_rows(filters=['SECURITY_CODE="999999"'], allow_empty=True) == []


# --------------------------------------------------------------------------- #
# 能力双登记对账
# --------------------------------------------------------------------------- #


class TestCapabilityRegistration:
    def test_registered_on_session(self) -> None:
        assert callable(WebQuoteSession("eastmoney").board_attribution)

    def test_declared_in_provider_registry(self) -> None:
        from atst.providers import PROVIDERS

        spec = PROVIDERS.get("eastmoney").channel("catalog")
        assert "board_attribution" in spec.capabilities

    def test_bound_to_eastmoney_catalog(self) -> None:
        bindings = [b for b in MIGRATED_BINDINGS if b.capability == "board_attribution"]
        assert len(bindings) == 1
        b = bindings[0]
        assert (b.provider, b.channel, b.backend, b.method) == (
            "eastmoney",
            "catalog",
            "web_session",
            "board_attribution",
        )
        assert b.source == "eastmoney"

    def test_status_is_alive(self) -> None:
        assert MIGRATED_CAPABILITY_STATUS["board_attribution"] == "alive"

    def test_client_construction_passes_capability_audit(self) -> None:
        """漏登记任一面的后果：Client() 构造即抛 CapabilityAuditError。"""
        from atst import Client

        Client()

    def test_typed_query_contract_requires_one_filter(self) -> None:
        from atst.errors import ValidationError
        from atst.typed_query import BoardAttributionQuery

        with pytest.raises(ValidationError):
            BoardAttributionQuery()
        assert BoardAttributionQuery(symbol="sh600519").capability == "board_attribution"
        assert BoardAttributionQuery(board_code="BK0896").board_code == "BK0896"


# --------------------------------------------------------------------------- #
# live 冒烟（需网络）
# --------------------------------------------------------------------------- #


@pytest.mark.network
class TestLive:
    def test_live_stock_to_boards(self) -> None:
        rows = WebQuoteSession("eastmoney").board_attribution("sh600519")
        assert rows, "贵州茅台应有所属板块"
        assert rows[0]["BOARD_RANK"] == 1
        assert rows[0]["BOARD_NAME"] == "食品饮料"
        assert all(r["NEW_BOARD_CODE"].startswith("BK") for r in rows if r["NEW_BOARD_CODE"])

    def test_live_board_to_members(self) -> None:
        rows = WebQuoteSession("eastmoney").board_attribution(board_code="BK0896")
        assert len(rows) > 30, "白酒板块应有数十只成分"
        assert {r["SECURITY_CODE"] for r in rows} >= {"600519", "000568", "000858"}

    def test_live_index_guard(self) -> None:
        assert WebQuoteSession("eastmoney").board_attribution("sh000001") == []
