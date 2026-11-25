# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3-2 低覆盖模块补离线单测：ResearchSessionMixin（sources/_session_research.py）。

全部离线：东财 datacenter 走假后端类、mofcom/chinamoney 走假共享 HTTP 客户端、
push2 clist 走假板块源；验证过滤拼装、字段重命名、客户端分页、干净失败
（WebSourceError）与"单类雷点失败不拖垮整体扫描"等公开行为。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources._base_http import HttpResponse
from atst.sources._session_research import ResearchSessionMixin, _pager

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 假后端
# --------------------------------------------------------------------------- #
class _FakeDcSource:
    """东财 datacenter-web 假后端：记录构造与 fetch_rows 参数。"""

    last_instance: _FakeDcSource | None = None
    canned: list[dict[str, Any]] = [{"row": 1}]

    def __init__(self, *, report: str | None = None, client: Any = None, **kwargs: Any) -> None:
        self.report = report
        self.client = client
        self.closed = False
        self.fetch_kwargs: dict[str, Any] | None = None
        _FakeDcSource.last_instance = self

    def fetch_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fetch_kwargs = kwargs
        return list(self.canned)

    def close(self) -> None:
        self.closed = True


class _FakeHttp:
    """假共享 HTTP 客户端：按 canned 响应回放 get/post。"""

    last_instance: _FakeHttp | None = None

    def __init__(self, get_resp: HttpResponse | None = None, post_resp: HttpResponse | None = None):
        self.get_resp = get_resp
        self.post_resp = post_resp
        self.calls: list[tuple[str, str]] = []
        _FakeHttp.last_instance = self

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("get", url))
        if self.get_resp is None:
            raise RuntimeError("fake http: no canned GET response")
        return self.get_resp

    def post(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append(("post", url))
        if self.post_resp is None:
            raise RuntimeError("fake http: no canned POST response")
        return self.post_resp


def _inst_kwargs() -> dict[str, Any]:
    inst = _FakeDcSource.last_instance
    assert inst is not None and inst.closed, "后端实例必须被 try/finally close()"
    return inst.fetch_kwargs or {}


# --------------------------------------------------------------------------- #
# 交易日历（内置离线部分）
# --------------------------------------------------------------------------- #
class TestTradeCalendar:
    def test_range_mode_weekdays(self) -> None:
        """#1 区间模式：返回交易日列表且全部 trading=True。"""
        rows = ResearchSessionMixin.trade_calendar(start="2026-01-01", end="2026-01-09")
        assert rows
        assert all(r["trading"] is True for r in rows)
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)
        # 2026-01-01 元旦休市（内置表）；工作日周末不含
        assert "2026-01-01" not in dates
        assert "2026-01-05" in dates  # 周一

    def test_summary_mode_per_year(self) -> None:
        """#2 摘要模式：按年份返回休市日摘要与 estimated 标记。"""
        rows = ResearchSessionMixin.trade_calendar()
        years = [r["year"] for r in rows]
        assert years == sorted(years)
        by_year = {r["year"]: r for r in rows}
        assert by_year[2026]["estimated"] is True
        assert by_year[2026]["holidays"] == sorted(by_year[2026]["holidays"])
        assert by_year[2026]["trading_day_count"] > 200

    def test_market_guard(self) -> None:
        """#3 非-cn 市场显式报错（干净失败）。"""
        with pytest.raises(ValueError, match="market='cn'"):
            ResearchSessionMixin.trade_calendar(market="hk")


# --------------------------------------------------------------------------- #
# 交易日历（在线校准，全局日历隔离）
# --------------------------------------------------------------------------- #
class TestTradingCalendar:
    def test_year_range_guard(self) -> None:
        """#4 年份越界前置报错。"""
        with pytest.raises(ValueError, match="超出合理范围"):
            ResearchSessionMixin.trading_calendar(year=1899)
        with pytest.raises(ValueError, match="超出合理范围"):
            ResearchSessionMixin.trading_calendar(year=2101)

    def test_online_calibration_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#5 在线校准成功：freshness→online、fallback=False（用全新日历隔离全局态）。"""
        from atst.domain.calendar import TradingCalendar

        monkeypatch.setattr(
            ResearchSessionMixin,
            "_online_holidays",
            staticmethod(lambda year: ["2026-01-01", "2026-10-01"]),
        )
        monkeypatch.setattr("atst.domain.calendar.get_calendar", TradingCalendar)
        out = ResearchSessionMixin.trading_calendar(year=2026)
        row = out[0]
        assert row["year"] == 2026
        assert row["freshness"] == "online"
        assert row["source"] == "eastmoney"
        assert row["fallback"] is False
        assert row["covered"] is True
        assert "2026-01-01" in row["holidays"]

    def test_online_failure_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#6 在线校准失败：回落内置表、fallback=True，不静默降级。"""

        def _boom(year: int) -> list[str]:
            raise RuntimeError("upstream down")

        monkeypatch.setattr(ResearchSessionMixin, "_online_holidays", staticmethod(_boom))
        out = ResearchSessionMixin.trading_calendar(year=2026)
        row = out[0]
        assert row["fallback"] is True
        assert row["freshness"] != "online"
        assert row["estimated"] is True  # 2026 内置为估算值
        assert row["trading_day_count"] > 200


# --------------------------------------------------------------------------- #
# 东财 datacenter 报表族（假后端）
# --------------------------------------------------------------------------- #
class TestEastmoneyReportMethods:
    def test_macro_pmi_and_lpr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#7 PMI/LPR：报表名与排序键。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        ResearchSessionMixin.macro_pmi(size=9)
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPT_ECONOMY_PMI"
        kw = _inst_kwargs()
        assert kw["sort_columns"] == "REPORT_DATE" and kw["size"] == 9
        ResearchSessionMixin.macro_lpr(page=2)
        kw = _inst_kwargs()
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPTA_WEB_RATE"
        assert kw["sort_columns"] == "TRADE_DATE" and kw["page"] == 2

    def test_macro_bond_yield_full_rename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#8 国债收益率：报表字段按期限映射重命名，日期截断到 10 位。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        _FakeDcSource.canned = [
            {"SOLAR_DATE": "2026-09-30 00:00:00", "EMM00166466": 1.72, "EMG00001310": 4.05}
        ]
        rows = ResearchSessionMixin.macro_bond_yield()
        assert rows == [
            {
                "date": "2026-09-30",
                "cn_10y": 1.72,
                "us_10y": 4.05,
            }
        ]
        kw = _inst_kwargs()
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPTA_WEB_TREASURYYIELD"
        assert kw["sort_columns"] == "SOLAR_DATE"

    def test_macro_bond_yield_single_term(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#9 单期限模式：只返回 {date, term, yield}。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        _FakeDcSource.canned = [{"SOLAR_DATE": "2026-09-30", "EMM00588704": 1.45}]
        rows = ResearchSessionMixin.macro_bond_yield(term="CN_2Y")
        assert rows == [{"date": "2026-09-30", "term": "cn_2y", "yield": 1.45}]

    def test_macro_bond_yield_invalid_term(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#10 非法期限干净失败。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        _FakeDcSource.canned = [{"SOLAR_DATE": "2026-09-30"}]
        with pytest.raises(ValueError, match="term"):
            ResearchSessionMixin.macro_bond_yield(term="cn_99y")

    def test_equity_pledge_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#11 股权质押：symbol/date 转过滤条件。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        ResearchSessionMixin.equity_pledge(symbol="sh600519", date="2026-09-26")
        kw = _inst_kwargs()
        assert kw["filters"] == ['SECURITY_CODE="600519"', 'TRADE_DATE="2026-09-26"']
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPT_CSDC_LIST"

    def test_valuation_history_count_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#12 估值历史：count 越界/非法值前置报错。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        for bad in (0, 501, True, "120"):
            with pytest.raises(ValueError, match="count"):
                ResearchSessionMixin.valuation_history("600519", count=bad)
        _FakeDcSource.canned = []
        ResearchSessionMixin.valuation_history("sh600519", count=120)
        kw = _inst_kwargs()
        assert kw["filters"] == ['SECURITY_CODE="600519"']
        assert kw["size"] == 120 and kw["page"] == 1

    def test_sw_industry_and_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#13 申万分类/变迁史：报表名与过滤。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        ResearchSessionMixin.sw_industry(symbol="600519")
        kw = _inst_kwargs()
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPT_SW_INDUSTRY"
        assert kw["filters"] == ['SECURITY_CODE="600519"']
        ResearchSessionMixin.sw_industry_history("sz000001")
        kw = _inst_kwargs()
        assert _FakeDcSource.last_instance is not None
        assert _FakeDcSource.last_instance.report == "RPT_SW_INDUSTRY_HIS"
        assert kw["filters"] == ['SECURITY_CODE="000001"']
        assert kw["sort_columns"] == "EFFECT_DATE"

    def test_index_valuation_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#14 指数估值：index_code 过滤，缺省全指数。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        ResearchSessionMixin.index_valuation(index_code="000300")
        kw = _inst_kwargs()
        assert kw["filters"] == ['INDEX_CODE="000300"']
        ResearchSessionMixin.index_valuation()
        kw = _inst_kwargs()
        assert list(kw["filters"]) == []

    def test_futures_and_options_rank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#15 期货/期权持仓排名：INSTRUMENT_ID / OPTION_ID 过滤。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeDcSource)
        ResearchSessionMixin.futures_position_rank(symbol="rb2601", date="2026-09-26")
        kw = _inst_kwargs()
        assert kw["filters"] == ['INSTRUMENT_ID="rb2601"', 'TRADE_DATE="2026-09-26"']
        ResearchSessionMixin.options_position_rank(symbol="io2601")
        kw = _inst_kwargs()
        assert kw["filters"] == ['OPTION_ID="io2601"']


# --------------------------------------------------------------------------- #
# push2 clist / ETF 份额
# --------------------------------------------------------------------------- #
class _FakeBoardSource:
    """东财板块源假后端：_get_json 直接回放 canned payload。"""

    last_instance: _FakeBoardSource | None = None
    payload: dict[str, Any] = {"ok": 1}
    rows: list[dict[str, Any]] = []

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.closed = False
        _FakeBoardSource.last_instance = self

    @property
    def source_name(self) -> str:
        return "cn_board"

    @property
    def rate_limiter(self):  # noqa: ANN201 - 测试桩
        return self

    def acquire(self, name: str) -> bool:
        return True

    def _get_json(self, path: str) -> dict[str, Any]:
        self.path = path
        return dict(self.payload)

    @staticmethod
    def _diff(payload: dict[str, Any]) -> list[dict[str, Any]]:
        return list(_FakeBoardSource.rows)

    def close(self) -> None:
        self.closed = True


class TestEtfShares:
    def test_em_clist_rows_request_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#16 _em_clist_rows：URL 带分页/fs/fields，diff 行转 dict。"""
        from atst.sources import _session_research

        monkeypatch.setattr("atst.sources.boards.EastmoneyBoardSource", _FakeBoardSource)
        _FakeBoardSource.rows = [{"f12": "510300", "f38": 1234}]
        rows = _session_research._em_clist_rows("b:MK0021", "f12,f38", page=2, size=100, fid="f12")
        assert rows == [{"f12": "510300", "f38": 1234}]
        inst = _FakeBoardSource.last_instance
        assert inst is not None and inst.closed
        path = inst.path
        assert "pn=2" in path and "pz=100" in path
        assert "fid=f12" in path and "b%3AMK0021" in path or "b:MK0021" in path

    def test_etf_shares_paging_and_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#17 ETF 份额：满页续拉、短页终止；symbol 客户端过滤。"""
        pages = [
            [
                {"f12": "510300", "f13": "1", "f14": "沪深300ETF", "f2": 3.9, "f3": 1.2, "f38": 98}
                for _ in range(500)
            ],
            [{"f12": "159915", "f13": "0", "f14": "创业板ETF", "f2": 2.1, "f3": -0.5, "f38": 55}],
        ]
        calls: list[int] = []

        def fake_clist(fs: str, fields: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs.get("page", 0))
            return pages[len(calls) - 1]

        monkeypatch.setattr("atst.sources._session_research._em_clist_rows", fake_clist)
        rows = ResearchSessionMixin.etf_shares(size=100, page=1)
        assert calls == [1, 2]  # 第一页满页 → 续拉第二页短页终止
        assert len(rows) == 100  # size=100 截断（501 行取前 100）
        assert rows[0]["code"] == "510300"

        calls.clear()  # 第二次调用从第 1 页重新计
        rows = ResearchSessionMixin.etf_shares(symbol="159915")
        assert [r["code"] for r in rows] == ["159915"]
        assert rows[0]["shares"] == 55

    def test_etf_shares_symbol_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#18 指定 symbol 不在场内名单：显式报错而非静默空。"""

        def fake_clist(fs: str, fields: str, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"f12": "510300", "f13": "1", "f14": "沪深300ETF", "f38": 98}]

        monkeypatch.setattr("atst.sources._session_research._em_clist_rows", fake_clist)
        with pytest.raises(ValueError, match="510999"):
            ResearchSessionMixin.etf_shares(symbol="510999")


# --------------------------------------------------------------------------- #
# 商务部社融 / chinamoney 回购定盘（假共享 HTTP）
# --------------------------------------------------------------------------- #
class TestMacroHttpEndpoints:
    def test_social_financing_mapping_and_pager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#19 社融：mofcom 字段映射 + 客户端分页。"""
        raw = [
            {
                "date": "202604",
                "tiosfs": 1,
                "rmblaon": 2,
                "forcloan": 3,
                "entrustloan": 4,
                "trustloan": 5,
                "ndbab": 6,
                "bibae": 7,
                "sfinfe": 8,
            },
            {"date": "202603", "tiosfs": 11},
            "not-a-dict",
        ]
        http = _FakeHttp(post_resp=HttpResponse(status=200, body=json.dumps(raw).encode()))
        monkeypatch.setattr("atst.sources._session_research._shared_http", lambda: http)
        rows = ResearchSessionMixin.macro_social_financing(size=1, page=2)
        assert rows == [
            {
                "month": "202603",
                "sfs_increment": 11,
                "rmb_loans": None,
                "forex_loans": None,
                "entrust_loans": None,
                "trust_loans": None,
                "undiscounted_bankers": None,
                "corporate_bonds": None,
                "equity_financing": None,
            }
        ]
        calls = http.calls
        assert calls and calls[0][0] == "post"
        assert "mofcom" in calls[0][1]

    def test_social_financing_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#20 社融：HTTP 失败 / 非 JSON / 非列表结构 → WebSourceError。"""
        monkeypatch.setattr(
            "atst.sources._session_research._shared_http",
            lambda: _FakeHttp(post_resp=HttpResponse(status=500, body=b"boom")),
        )
        with pytest.raises(WebSourceError, match="HTTP 500"):
            ResearchSessionMixin.macro_social_financing()
        monkeypatch.setattr(
            "atst.sources._session_research._shared_http",
            lambda: _FakeHttp(post_resp=HttpResponse(status=200, body=b"<html/>")),
        )
        with pytest.raises(WebSourceError, match="解析失败"):
            ResearchSessionMixin.macro_social_financing()
        monkeypatch.setattr(
            "atst.sources._session_research._shared_http",
            lambda: _FakeHttp(post_resp=HttpResponse(status=200, body=b'{"k": 1}')),
        )
        with pytest.raises(WebSourceError, match="结构异常"):
            ResearchSessionMixin.macro_social_financing()

    def _csv_http(self, monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
        monkeypatch.setattr(
            "atst.sources._session_research._shared_http",
            lambda: _FakeHttp(get_resp=HttpResponse(status=200, body=body)),
        )

    def test_repo_rate_frr_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#21 回购定盘 FRR：CSV 空列剔除 + 列映射。"""
        csv = (
            "date,FR001,FR007,FR014,,\r\n"
            "2026-09-30,1.52,1.65,1.80,,\r\n"
            "2026-09-29,1.51,1.64,1.70\r\n"
        )
        self._csv_http(monkeypatch, csv.encode())
        rows = ResearchSessionMixin.macro_repo_rate(kind="frr")
        assert rows[0] == {"date": "2026-09-30", "FR001": "1.52", "FR007": "1.65", "FR014": "1.80"}
        assert rows[1]["FR014"] == "1.70"
        assert rows[1]["date"] == "2026-09-29"
        http = _FakeHttp.last_instance
        assert http is not None and http.calls[0][1].endswith("frr-chrt.csv")

    def test_repo_rate_fdr_and_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#22 FDR kind、非法 kind、HTTP 失败、空 CSV → 干净失败。"""
        self._csv_http(monkeypatch, b"date,FDR001,FDR007,FDR014\n2026-09-30,1.50,1.62,1.77\n")
        rows = ResearchSessionMixin.macro_repo_rate(kind="fdr")
        assert rows[0]["FDR001"] == "1.50"
        http = _FakeHttp.last_instance
        assert http is not None and http.calls[0][1].endswith("fdr-chrt.csv")
        with pytest.raises(ValueError, match="frr"):
            ResearchSessionMixin.macro_repo_rate(kind="xyz")
        monkeypatch.setattr(
            "atst.sources._session_research._shared_http",
            lambda: _FakeHttp(get_resp=HttpResponse(status=503, body=b"")),
        )
        with pytest.raises(WebSourceError, match="HTTP 503"):
            ResearchSessionMixin.macro_repo_rate()
        self._csv_http(monkeypatch, b"date,FDR001\n")
        with pytest.raises(WebSourceError, match="返回为空"):
            ResearchSessionMixin.macro_repo_rate()


# --------------------------------------------------------------------------- #
# 其它路由 / 扫雷
# --------------------------------------------------------------------------- #
class TestRoutesAndScan:
    def test_st_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#23 ST 名单：BK0511 成员、limit/page 透传、后端关闭。"""
        captured: dict[str, Any] = {}

        class _FakeBoard(_FakeBoardSource):
            def fetch_members(self, code: str, *, limit: int = 20, page: int = 1):
                captured.update(code=code, limit=limit, page=page)
                return [{"code": "000001"}]

        monkeypatch.setattr("atst.sources.boards.EastmoneyBoardSource", _FakeBoard)
        rows = ResearchSessionMixin.st_list(limit=10, page=2)
        assert rows == [{"code": "000001"}]
        assert captured == {"code": "BK0511", "limit": 10, "page": 2}
        assert _FakeBoardSource.last_instance is not None
        assert _FakeBoardSource.last_instance.closed

    def test_interactive_qa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#24 互动易问答：参数透传给巨潮源。"""
        captured: dict[str, Any] = {}

        class _FakeCninfo:
            def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
                self.closed = False

            def fetch_interactive_qa(self, **kwargs: Any):
                captured.update(kwargs)
                return [{"question": "q"}]

            def close(self) -> None:
                self.closed = True

        monkeypatch.setattr("atst.sources.cninfo.adapters.CninfoSource", _FakeCninfo)
        rows = ResearchSessionMixin.interactive_qa(symbol="600519", keyword="分红", size=9)
        assert rows == [{"question": "q"}]
        assert captured == {"symbol": "600519", "keyword": "分红", "page": 1, "size": 9}

    def test_news_broadcast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#25 新闻联播文字稿：date/page/size 透传给 news 模块。"""
        captured: dict[str, Any] = {}

        def fake_fetch(**kwargs: Any):
            captured.update(kwargs)
            return [{"date": "2026-09-26"}]

        monkeypatch.setattr("atst.sources.news.fetch_cctv_news_broadcast", fake_fetch)
        rows = ResearchSessionMixin.news_broadcast(date="2026-09-26", page=2, size=10)
        assert rows == [{"date": "2026-09-26"}]
        assert captured == {"date": "2026-09-26", "page": 2, "size": 10}

    def test_risk_scan_aggregates_and_isolates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#26 扫雷：商誉+质押按 kind 聚合；单类失败不拖垮整体。"""
        calls: list[str] = []

        def fake_rows(report: str, **kwargs: Any):
            calls.append(report)
            if report == "RPT_GOODWILL_STOCKDETAILS":
                return [{"NOTICE_DATE": "2026-01-01"}]
            raise WebSourceError("pledge upstream down")

        monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", fake_rows)
        rows = ResearchSessionMixin.risk_scan("600519")
        assert calls == ["RPT_GOODWILL_STOCKDETAILS", "RPT_CSDC_LIST"]
        assert rows == [{"kind": "goodwill", "NOTICE_DATE": "2026-01-01"}]

        def ok_rows(report: str, **kwargs: Any):
            return [{"v": report}]

        monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", ok_rows)
        rows = ResearchSessionMixin.risk_scan("sh600519")
        assert [r["kind"] for r in rows] == ["goodwill", "pledge"]


def test_pager_bounds() -> None:
    """#27 _pager：越界页返回空、page/size 下限钳制。"""
    rows = [{"i": i} for i in range(5)]
    assert _pager(rows, size=2, page=1) == rows[:2]
    assert _pager(rows, size=2, page=3) == rows[4:]
    assert _pager(rows, size=2, page=99) == []
    assert _pager(rows, size=0, page=0) == rows[:1]  # max(1, size) / max(1, page)
