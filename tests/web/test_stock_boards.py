"""个股所属板块 / IPO 申购日历 / 大单流向 测试。

罐头响应取自 2026-09 实测抓包（slist spt=3 / datacenter RPTA_APP_IPOAPPLY），
全部离线不发起真实 HTTP；live 冒烟单独标注 ``network``，网络受限自动跳过。
"""

from __future__ import annotations

import json

import pytest

from atst.errors import SourceDeprecated, WebSourceError
from atst.web.base import HttpResponse, RateLimiter
from atst.web.corporate import EastmoneyIpoSource
from atst.web.session import WebQuoteSession
from atst.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit


class FakeHttp:
    """返回预置响应的假 HTTP 客户端。"""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# 个股所属板块（slist spt=3）
# --------------------------------------------------------------------------- #
#: 罐头：格力电器所属板块（实测截选，按 f3 降序）
SLIST_CANNED = json.dumps(
    {
        "rc": 0,
        "rt": 18,
        "svr": 175648106,
        "lt": 1,
        "full": 1,
        "dlmkts": "",
        "data": {
            "total": 38,
            "diff": [
                {"f3": 0.84, "f12": "BK1102", "f13": 90, "f14": "空气能热泵", "f152": 2},
                {"f3": 0.17, "f12": "BK1239", "f13": 90, "f14": "白色家电", "f152": 2},
                {"f3": -0.03, "f12": "BK1138", "f13": 90, "f14": "液冷服务器", "f152": 2},
            ],
        },
    }
).encode("utf-8")


def _boards_src(body: bytes = SLIST_CANNED, status: int = 200):
    from atst.web.boards import EastmoneyBoardSource

    src = EastmoneyBoardSource(max_retries=0)
    src.client = FakeHttp(body, status=status)
    src.rate_limiter = RateLimiter()
    return src


class TestStockBoards:
    def test_parse_rows(self) -> None:
        rows = _boards_src().fetch_stock_boards("sz000651")
        assert rows[0] == {
            "code": "BK1102",
            "name": "空气能热泵",
            "pct_change": 0.84,
            "market": 90,
        }
        assert len(rows) == 3

    def test_secid_in_url(self) -> None:
        src = _boards_src()
        src.fetch_stock_boards("600519")
        url = src.client.calls[0]
        assert "/api/qt/slist/get?" in url
        assert "spt=3" in url and "secid=1.600519" in url

    def test_empty_data(self) -> None:
        body = json.dumps({"rc": 0, "data": None}).encode("utf-8")
        assert _boards_src(body).fetch_stock_boards("sz000001") == []

    def test_bad_json_raises_deprecated(self) -> None:
        with pytest.raises(SourceDeprecated):
            _boards_src(b"not-json").fetch_stock_boards("sz000001")

    def test_http_error(self) -> None:
        with pytest.raises(WebSourceError):
            _boards_src(status=500).fetch_stock_boards("sz000001")

    def test_facade_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "atst.web.boards.EastmoneyBoardSource.fetch_stock_boards",
            lambda self, symbol: [{"code": "BK0456", "name": "家用电器"}],
        )
        assert WebQuoteSession.stock_boards("000651")[0]["code"] == "BK0456"
        assert WebQuoteSession.stock_boards("000651")[0]["code"] == "BK0456"

    def test_spec_capability(self) -> None:
        assert "stock_boards" in KNOWN_SOURCES["eastmoney"].capabilities


# --------------------------------------------------------------------------- #
# IPO 申购日历（datacenter RPTA_APP_IPOAPPLY）
# --------------------------------------------------------------------------- #
#: 罐头：实测行（未定价 / 未上市 → null）
IPO_CANNED = json.dumps(
    {
        "version": "x",
        "success": True,
        "message": "ok",
        "code": 0,
        "result": {
            "pages": 1876,
            "data": [
                {
                    "SECUCODE": "301686.SZ",
                    "SECURITY_CODE": "301686",
                    "APPLY_CODE": "301686",
                    "APPLY_DATE": "2026-09-10 00:00:00",
                    "LISTING_DATE": None,
                    "TRADE_MARKET": "深圳证券交易所",
                    "MARKET_TYPE_NEW": "深交所其他",
                    "MARKET_TYPE": "非科创板",
                    "BALLOT_PAY_DATE": "2026-09-14 00:00:00",
                    "BALLOT_NUM_DATE": "2026-09-14 00:00:00",
                    "ONLINE_ISSUE_DATE": "2026-09-10 00:00:00",
                    "ISSUE_PRICE": None,
                    "PREDICT_ISSUE_PRICE": 0,
                    "ISSUE_NUM": 1233.29,
                    "ONLINE_ISSUE_NUM": 2737500,
                    "ONLINE_APPLY_UPPER": 2500,
                    "TOP_APPLY_MARKETCAP": 2.5,
                    "AFTER_ISSUE_PE": None,
                    "INDUSTRY_PE": 28.4,
                    "BVPS": 17.6004,
                    "ISSUE_WAY": "网上定价发行,市值申购",
                }
            ],
        },
    }
).encode("utf-8")


def _ipo_src(body: bytes = IPO_CANNED):
    src = EastmoneyIpoSource(max_retries=0)
    src.client = FakeHttp(body)
    src.rate_limiter = RateLimiter()
    return src


class TestIpoCalendar:
    def test_parse_row_null_semantics(self) -> None:
        rows = _ipo_src().fetch_ipo()
        row = rows[0]
        assert row["code"] == "301686"
        assert row["name"] == ""  # 罐头省略 SECURITY_NAME → 空串
        assert row["apply_date"] == "2026-09-10"  # 截到日期
        assert row["listing_date"] == ""  # null → 空串
        assert row["issue_price"] is None  # 未定价 → None（不填 0）
        assert row["predict_issue_price"] == 0.0  # 显式 0 保留
        assert row["industry_pe"] == 28.4
        assert row["online_issue_num"] == 2737500
        assert row["online_apply_upper"] == 2500
        assert row["top_apply_marketcap"] == 2.5

    def test_apply_date_filter_in_url(self) -> None:
        src = _ipo_src()
        src.fetch_ipo(apply_date="2026-09-10")
        url = src.client.calls[0]
        assert "reportName=RPTA_APP_IPOAPPLY" in url
        assert "sortColumns=APPLY_DATE" in url
        assert "APPLY_DATE%3D%222026-09-10%2000%3A00%3A00%22" in url

    def test_no_filter_no_filter_param(self) -> None:
        src = _ipo_src()
        src.fetch_ipo()
        assert "filter=" not in src.client.calls[0]

    def test_failure_response(self) -> None:
        body = json.dumps({"success": False, "message": "报表不存在", "code": 9501}).encode("utf-8")
        with pytest.raises(WebSourceError, match="9501"):
            _ipo_src(body).fetch_ipo()

    def test_facade_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "atst.web.corporate.EastmoneyIpoSource.fetch_ipo",
            lambda self, **kw: [{"code": "301686", "apply_date": "2026-09-10"}],
        )
        rows = WebQuoteSession.ipo_calendar(apply_date="2026-09-10")
        assert rows[0]["code"] == "301686"
        assert WebQuoteSession.ipo_calendar()[0]["code"] == "301686"

    def test_spec_capability(self) -> None:
        assert "ipo" in KNOWN_SOURCES["corporate"].capabilities


# --------------------------------------------------------------------------- #
# 大单流向（fund_flow 单股语义别名）
# --------------------------------------------------------------------------- #
class TestBigOrderFlow:
    def test_returns_first_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_flow(self, symbols):  # noqa: ANN001, ARG001
            captured["symbols"] = list(symbols)
            return [{"code": "000651", "main_net": 1.2e8, "main_net_ratio": 5.2}]

        monkeypatch.setattr("atst.web.fundflow.EastmoneyFundFlowSource.fetch_flow", fake_flow)
        row = WebQuoteSession.big_order_flow("000651")
        assert captured["symbols"] == ["000651"]
        assert row is not None and row["main_net_ratio"] == 5.2
        assert WebQuoteSession.big_order_flow("000651") is not None

    def test_none_when_no_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "atst.web.fundflow.EastmoneyFundFlowSource.fetch_flow",
            lambda self, symbols: [],
        )
        assert WebQuoteSession.big_order_flow("999999") is None


# --------------------------------------------------------------------------- #
# live 冒烟（网络护栏：不可达 / 受限时跳过，只验证结构有效性）
# --------------------------------------------------------------------------- #
@pytest.mark.network
class TestLiveSmoke:
    def _guarded(self, fn):
        from atst.errors import TdxError

        try:
            return fn()
        except (TdxError, OSError, TimeoutError) as exc:
            pytest.skip(f"东财网络不可达或受限：{exc}")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")

    def test_stock_boards_live(self) -> None:
        rows = self._guarded(lambda: WebQuoteSession.stock_boards("000651"))
        assert rows and all(r["code"].startswith("BK") for r in rows)
        assert all(isinstance(r["pct_change"], (int, float)) for r in rows)

    def test_ipo_live(self) -> None:
        rows = self._guarded(lambda: WebQuoteSession.ipo_calendar(size=5))
        assert isinstance(rows, list)
        for r in rows:
            assert r["code"] and r["apply_date"]
            assert r["issue_price"] is None or r["issue_price"] > 0

    def test_big_order_flow_live(self) -> None:
        row = self._guarded(lambda: WebQuoteSession.big_order_flow("000651"))
        if row is None:  # 非交易时段 ulist 可能返回空 diff
            pytest.skip("资金流暂无数据（非交易时段）")
        assert row["main_net"] == row["main_net"]  # NaN 检查（自反性）
