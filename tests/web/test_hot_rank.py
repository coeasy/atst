"""盘中异动池 / 股吧人气榜 测试。

罐头响应取自 2026-09 实测抓包（push2ex getAllStockChanges 交易时段实时样本 /
emappdata stockrank），全部离线；live 冒烟标注 ``network``，网络受限自动跳过。
"""

from __future__ import annotations

import json

import pytest

from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.base import HttpResponse, RateLimiter
from tstdx.web.facade import WebQuoteSession
from tstdx.web.fundflow import EastmoneyStockChangesSource
from tstdx.web.hot_rank import EastmoneyHotRankSource
from tstdx.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit


class FakeHttp:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[tuple[str, bytes | None]] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append((url, None))
        return HttpResponse(self.status, self.body, {})

    def post(self, url, *, body=None, **kwargs):  # noqa: ARG002
        self.calls.append((url, body))
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# 盘中异动（getAllStockChanges）
# --------------------------------------------------------------------------- #
#: 罐头：交易时段实时样本（火箭发射 + 快速反弹）
CHANGES_CANNED = json.dumps(
    {
        "rc": 0,
        "rt": 105,
        "svr": 181735295,
        "lt": 2,
        "full": 0,
        "data": {
            "tc": 2091,
            "allstock": [
                {
                    "tm": 111905,
                    "c": "920222",
                    "m": 0,
                    "n": "益坤电气",
                    "t": 8201,
                    "i": "0.181744,29.00000,0.181744",
                },
                {
                    "tm": 111903,
                    "c": "600186",
                    "m": 1,
                    "n": "莲花控股",
                    "t": 8202,
                    "i": "0.025795,11.93000,0.025795",
                },
                {"tm": 111901, "c": "000001", "m": 0, "n": "平安银行", "t": 8193, "i": "-"},
            ],
        },
    }
).encode("utf-8")


def _changes_src(body: bytes = CHANGES_CANNED, status: int = 200):
    src = EastmoneyStockChangesSource(max_retries=0)
    src.client = FakeHttp(body, status=status)
    src.rate_limiter = RateLimiter()
    return src


class TestStockChanges:
    def test_parse_rows(self) -> None:
        rows = _changes_src().fetch_changes()
        assert rows[0]["time"] == "11:19:05"
        assert rows[0]["code"] == "920222"
        assert rows[0]["change_type"] == 8201
        assert rows[0]["change_name"] == "火箭发射"
        assert rows[0]["metrics"] == [0.181744, 29.0, 0.181744]
        assert rows[0]["total"] == 2091
        assert len(rows) == 3

    def test_dash_metrics_tolerated(self) -> None:
        """``i`` 为 ``-``（无指标）时 metrics 为空列表，不抛错。"""
        rows = _changes_src().fetch_changes()
        assert rows[2]["metrics"] == []
        assert rows[2]["change_name"] == "大笔买入"

    def test_url_default_all_types(self) -> None:
        src = _changes_src()
        src.fetch_changes()
        url = src.client.calls[0][0]
        assert "getAllStockChanges" in url
        assert "dpt=wzchanges" in url
        assert "8201" in url and "8193" in url  # 空 types → 全部 16 类

    def test_url_type_filter_and_pagination(self) -> None:
        src = _changes_src()
        src.fetch_changes((8201,), page=3, size=10)
        url = src.client.calls[0][0]
        assert "type=8201" in url
        assert "pageindex=2" in url and "pagesize=10" in url

    def test_invalid_types_dropped(self) -> None:
        src = _changes_src()
        src.fetch_changes((8201, 9999))
        url = src.client.calls[0][0]
        assert "type=8201" in url and "9999" not in url

    def test_non_json_raises_deprecated(self) -> None:
        with pytest.raises(SourceDeprecated):
            _changes_src(b"not-json").fetch_changes()

    def test_http_error(self) -> None:
        with pytest.raises(WebSourceError):
            _changes_src(status=500).fetch_changes()

    def test_empty_allstock_is_legal(self) -> None:
        """非交易时段 allstock 为空 → []（合法状态，不抛错）。"""
        body = json.dumps({"rc": 0, "data": {"tc": 0, "allstock": []}}).encode("utf-8")
        assert _changes_src(body).fetch_changes() == []

    def test_change_types_enum_complete(self) -> None:
        """16 类枚举键连续可查（8193-8196, 8201-8216）。"""
        et = EastmoneyStockChangesSource.CHANGE_TYPES
        assert len(et) == 20
        assert et[8201] == "火箭发射" and et[8193] == "大笔买入"
        assert et[8216] == "60日大幅下跌"

    def test_facade_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tstdx.web.fundflow.EastmoneyStockChangesSource.fetch_changes",
            lambda self, types=(), *, page=1, size=50: [{"change_type": 8201}],
        )
        assert WebQuoteSession.stock_changes((8201,))[0]["change_type"] == 8201
        assert WebQuoteSession.stock_changes()[0]["change_type"] == 8201

    def test_spec_capability(self) -> None:
        assert "stock_changes" in KNOWN_SOURCES["stock_changes"].capabilities


# --------------------------------------------------------------------------- #
# 股吧人气榜（emappdata stockrank POST）
# --------------------------------------------------------------------------- #
#: 罐头：实测响应（截选前 3）
RANK_CANNED = json.dumps(
    {
        "globalId": "786e4c21-70dc-435a-93bb-38",
        "message": "OK",
        "status": 0,
        "code": 0,
        "data": [
            {"sc": "SH600127", "rk": 1, "rc": 0, "hisRc": 0},
            {"sc": "SZ003040", "rk": 2, "rc": 0, "hisRc": 6},
            {"sc": "SH601086", "rk": 3, "rc": 3, "hisRc": -1},
        ],
    }
).encode("utf-8")


def _rank_src(body: bytes = RANK_CANNED, status: int = 200):
    src = EastmoneyHotRankSource(max_retries=0)
    src.client = FakeHttp(body, status=status)
    src.rate_limiter = RateLimiter()
    return src


class TestHotRank:
    def test_parse_rows_sorted(self) -> None:
        rows = _rank_src().fetch_hot_rank()
        assert [r["rank"] for r in rows] == [1, 2, 3]
        assert rows[0] == {
            "rank": 1,
            "symbol": "sh600127",
            "code": "600127",
            "market": "sh",
            "rank_change": 0,
            "his_rank_change": 0,
        }
        assert rows[2]["his_rank_change"] == -1

    def test_post_body_shape(self) -> None:
        """POST JSON 体：appId 公开参数 + globalId 本库生成 + 分页。"""
        src = _rank_src()
        src.fetch_hot_rank(page=2, size=50)
        url, body = src.client.calls[0]
        assert url.endswith("/stockrank/getAllCurrentList")
        payload = json.loads(body)
        assert payload["appId"] == "appId01"
        assert payload["pageNo"] == 2 and payload["pageSize"] == 50
        assert payload["marketType"] == ""
        assert len(payload["globalId"]) == 36  # uuid4

    def test_size_clamped_to_100(self) -> None:
        src = _rank_src()
        src.fetch_hot_rank(size=500)
        assert json.loads(src.client.calls[0][1])["pageSize"] == 100

    def test_bad_status_raises(self) -> None:
        body = json.dumps({"status": 1, "message": "blocked"}).encode("utf-8")
        with pytest.raises(WebSourceError, match="blocked"):
            _rank_src(body).fetch_hot_rank()

    def test_non_json_raises_deprecated(self) -> None:
        with pytest.raises(SourceDeprecated):
            _rank_src(b"not-json").fetch_hot_rank()

    def test_short_sc_skipped(self) -> None:
        body = json.dumps(
            {
                "status": 0,
                "data": [
                    {"sc": "X", "rk": 1},
                    {"sc": "SZ000001", "rk": 2},
                ],
            }
        ).encode("utf-8")
        rows = _rank_src(body).fetch_hot_rank()
        assert [r["code"] for r in rows] == ["000001"]

    def test_facade_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tstdx.web.hot_rank.EastmoneyHotRankSource.fetch_hot_rank",
            lambda self, *, page=1, size=100: [{"rank": 1, "symbol": "sh600127"}],
        )
        assert WebQuoteSession.hot_rank()[0]["rank"] == 1
        assert WebQuoteSession.hot_rank(size=50)[0]["symbol"] == "sh600127"

    def test_spec_capability(self) -> None:
        assert "hot_rank" in KNOWN_SOURCES["hot_rank"].capabilities


# --------------------------------------------------------------------------- #
# live 冒烟（网络护栏）
# --------------------------------------------------------------------------- #
@pytest.mark.network
class TestLiveSmoke:
    def _guarded(self, fn):
        from tstdx.errors import TdxError

        try:
            return fn()
        except (TdxError, OSError, TimeoutError) as exc:
            pytest.skip(f"网络不可达或受限：{exc}")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")

    def test_stock_changes_live(self) -> None:
        rows = self._guarded(lambda: WebQuoteSession.stock_changes(size=10))
        assert isinstance(rows, list)
        for r in rows:
            assert r["code"] and r["change_name"]
            assert r["change_type"] in EastmoneyStockChangesSource.CHANGE_TYPES

    def test_hot_rank_live(self) -> None:
        rows = self._guarded(lambda: WebQuoteSession.hot_rank(size=20))
        assert rows and rows[0]["rank"] == 1
        assert all(r["symbol"].startswith(("sh", "sz", "bj")) for r in rows)
