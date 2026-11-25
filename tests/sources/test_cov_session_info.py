# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""资金流/基本面会话 Mixin 离线单测（覆盖率补强）。

两类覆盖：

* 真实本地逻辑——``limit_up_ladder`` 的池总数与连板分布、``market_breadth``
  的涨跌家数、``news(pages>1)`` 的跨页去重与提前终止、``dc_query`` 的
  symbol → filter 翻译（含无个股过滤键的报表）；
* 纯委托——假适配器验证参数透传、进程级共享 client 注入与 ``finally close()``。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources._session_info import CorporateSessionMixin, FundFlowSessionMixin

pytestmark = pytest.mark.unit


class _Fake:
    """通用假适配器：按类级 ``results`` 回放结果/异常，并记录调用。"""

    results: dict[str, Any] = {}
    instances: list[_Fake] = []

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.client = client
        self.kwargs = kwargs
        self.closed = False
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        type(self).instances.append(self)

    def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            handler = type(self).results.get(name, [])
            if isinstance(handler, Exception):
                raise handler
            if callable(handler):
                return handler(*args, **kwargs)
            return handler

        return _call


def _install(
    monkeypatch: pytest.MonkeyPatch, target: str, attr: str, **results: Any
) -> type[_Fake]:
    fake_cls = type(f"_Fake{attr}", (_Fake,), {"results": results})
    monkeypatch.setattr(f"{target}.{attr}", fake_cls)
    return fake_cls


@pytest.fixture(autouse=True)
def _clean_instances() -> Any:
    _Fake.instances.clear()
    yield
    _Fake.instances.clear()


@pytest.fixture(autouse=True)
def _shared(monkeypatch: pytest.MonkeyPatch) -> Any:
    sentinel = object()
    monkeypatch.setattr("atst.sources._session_info._shared_http", lambda: sentinel)
    return sentinel


# --------------------------------------------------------------------------- #
# 资金流
# --------------------------------------------------------------------------- #
def test_fund_flow_methods_delegate(monkeypatch: pytest.MonkeyPatch, _shared: Any) -> None:
    _install(
        monkeypatch,
        "atst.sources.fundflow",
        "EastmoneyFundFlowSource",
        fetch_flow=[{"main_net": 1}],
        fetch_history=[{"date": "2026-11-24"}],
    )
    assert FundFlowSessionMixin.fund_flow(["600519", "000001"]) == [{"main_net": 1}]
    assert FundFlowSessionMixin.fund_flow_history("600519", period="week", count=5) == [
        {"date": "2026-11-24"}
    ]
    assert FundFlowSessionMixin.big_order_flow("600519") == {"main_net": 1}

    flow, history, single = _Fake.instances
    assert flow.client is _shared
    assert flow.calls == [("fetch_flow", (["600519", "000001"],), {})]
    assert history.calls == [("fetch_history", ("600519",), {"period": "week", "count": 5})]
    assert single.calls == [("fetch_flow", (["600519"],), {})]
    assert all(inst.closed for inst in (flow, history, single))


def test_big_order_flow_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "atst.sources.fundflow", "EastmoneyFundFlowSource", fetch_flow=[])
    assert FundFlowSessionMixin.big_order_flow("600519") is None


def test_limit_pool_and_stock_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch, "atst.sources.fundflow", "EastmoneyLimitPoolSource", fetch_pool=[{"code": "1"}]
    )
    _install(
        monkeypatch,
        "atst.sources.fundflow",
        "EastmoneyStockChangesSource",
        fetch_changes=[{"code": "600186"}],
    )
    assert FundFlowSessionMixin.limit_pool("dt", date="20261124", page=2, limit=5) == [
        {"code": "1"}
    ]
    assert FundFlowSessionMixin.stock_changes((8201,), page=2, size=10) == [{"code": "600186"}]

    pool, changes = _Fake.instances
    assert pool.calls == [("fetch_pool", ("dt",), {"date": "20261124", "page": 2, "limit": 5})]
    assert changes.calls == [("fetch_changes", ((8201,),), {"page": 2, "size": 10})]
    assert pool.closed and changes.closed


def test_limit_up_ladder_aggregates_totals_and_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Pool:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, int]] = []
            self.closed = False

        def fetch_pool(
            self, pool: str, *, date: str = "", page: int = 1, limit: int = 20
        ) -> list[dict[str, Any]]:
            self.calls.append((pool, date, limit))
            return {
                "zt": [
                    {"total": 5, "limit_up_days": 3},
                    {"total": 5, "limit_up_days": 1},
                    {"total": 5, "limit_up_days": 3},
                ],
                "dt": [{"total": 2}],
                "zb": [{"total": 1}],
            }[pool]

        def close(self) -> None:
            self.closed = True

    pool = Pool()
    monkeypatch.setattr("atst.sources.fundflow.EastmoneyLimitPoolSource", lambda: pool)
    ladder = FundFlowSessionMixin.limit_up_ladder(date="20261124")
    assert ladder.limit_up_count == 5  # 取池 total，不是 len(rows)
    assert ladder.limit_down_count == 2
    assert ladder.broken_count == 1
    assert ladder.broken_rate == pytest.approx(1 / 6)
    assert ladder.highest_board == 3
    assert ladder.board_distribution == {1: 1, 3: 2}
    assert pool.calls == [("zt", "20261124", 500), ("dt", "20261124", 500), ("zb", "20261124", 500)]
    assert pool.closed is True


def test_limit_up_ladder_empty_pools_are_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    class Pool:
        def __init__(self) -> None:
            self.closed = False

        def fetch_pool(
            self, pool: str, *, date: str = "", page: int = 1, limit: int = 20
        ) -> list[dict[str, Any]]:
            return []

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("atst.sources.fundflow.EastmoneyLimitPoolSource", Pool)
    ladder = FundFlowSessionMixin.limit_up_ladder()
    assert (ladder.limit_up_count, ladder.limit_down_count, ladder.broken_count) == (0, 0, 0)
    assert ladder.broken_rate == 0.0
    assert ladder.highest_board == 0


def test_market_breadth_counts_and_pool_totals(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"change_pct": 1.0},
        {"change_pct": -2.0},
        {"change_pct": 0.0},
        {"change_pct": None},
    ]
    _install(monkeypatch, "atst.sources.fundflow", "EastmoneyRankSource", fetch_rows=rows)

    class Pool:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.closed = False

        def fetch_pool(self, pool: str, **kwargs: Any) -> list[dict[str, Any]]:
            self.calls.append(pool)
            return [{"total": {"zt": 3, "dt": 1, "zb": 2}[pool]}]

        def close(self) -> None:
            self.closed = True

    pool = Pool()
    monkeypatch.setattr("atst.sources.fundflow.EastmoneyLimitPoolSource", lambda: pool)
    breadth = FundFlowSessionMixin.market_breadth(market="all_a", limit=5000)
    assert (breadth.up, breadth.down, breadth.flat, breadth.total) == (1, 1, 2, 4)
    assert (breadth.limit_up, breadth.limit_down, breadth.broken) == (3, 1, 2)
    assert breadth.limit_up_ratio == pytest.approx(3 / 4)
    assert breadth.limit_down_ratio == pytest.approx(1 / 4)

    rank = _Fake.instances[0]
    assert rank.calls == [("fetch_rows", ("all_a",), {"sort": "change_pct", "limit": 5000})]
    assert rank.closed is True
    assert pool.calls == ["zt", "dt", "zb"]
    assert pool.closed is True


def test_sina_fund_flow_and_northbound(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.fundflow",
        "SinaFundFlowSource",
        fetch_stock_flow=[{"date": "2026-11-24"}],
        fetch_board_flow=[{"date": "2026-11-24"}],
    )
    _install(
        monkeypatch,
        "atst.sources.fundflow",
        "EastmoneyNorthboundSource",
        fetch_northbound=[{"day": "1"}],
    )
    assert FundFlowSessionMixin.sina_fund_flow("600519", page=2, size=5) == [{"date": "2026-11-24"}]
    assert FundFlowSessionMixin.sina_board_fund_flow("new_dzxx", page=3, size=6) == [
        {"date": "2026-11-24"}
    ]
    assert FundFlowSessionMixin.northbound() == [{"day": "1"}]

    stock, board, north = _Fake.instances
    assert stock.calls == [("fetch_stock_flow", ("600519",), {"page": 2, "size": 5})]
    assert board.calls == [("fetch_board_flow", ("new_dzxx",), {"page": 3, "size": 6})]
    assert north.calls == [("fetch_northbound", (), {})]
    assert all(inst.closed for inst in (stock, board, north))


# --------------------------------------------------------------------------- #
# 公司资料 / 公告 / 股东
# --------------------------------------------------------------------------- #
def test_profile_notices_reports_shareholders(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.corporate",
        "EastmoneyProfileSource",
        fetch_profile={"code": "600519"},
    )
    _install(
        monkeypatch, "atst.sources.corporate", "EastmoneyNoticeSource", fetch_notices=[{"id": 1}]
    )
    _install(
        monkeypatch, "atst.sources.corporate", "EastmoneyResearchSource", fetch_reports=[{"id": 2}]
    )
    _install(
        monkeypatch,
        "atst.sources.corporate",
        "EastmoneyShareholderSource",
        fetch_free_holders=[{"name": "茅台集团"}],
        fetch_holder_num=[{"num": 1}],
    )

    assert CorporateSessionMixin.profile("600519") == {"code": "600519"}
    assert CorporateSessionMixin.notices(["600519", "000001"], page=2, size=5) == [{"id": 1}]
    assert CorporateSessionMixin.reports("600519", page=3, size=6) == [{"id": 2}]
    assert CorporateSessionMixin.shareholders("600519", size=3) == [{"name": "茅台集团"}]
    assert CorporateSessionMixin.free_holders("600519", size=4) == [{"name": "茅台集团"}]
    assert CorporateSessionMixin.holder_num("600519", size=7) == [{"num": 1}]

    profile, notice, research, holder, free, holder_num = _Fake.instances
    assert profile.calls == [("fetch_profile", ("600519",), {})]
    assert notice.calls == [("fetch_notices", (["600519", "000001"],), {"page": 2, "size": 5})]
    assert research.calls == [("fetch_reports", ("600519",), {"page": 3, "size": 6})]
    assert holder.calls == [("fetch_free_holders", ("600519",), {"size": 3})]
    assert free.calls == [("fetch_free_holders", ("600519",), {"size": 4})]
    assert holder_num.calls == [("fetch_holder_num", ("600519",), {"size": 7})]
    assert all(inst.closed for inst in _Fake.instances)


def test_block_unlock_performance_forecast_ipo(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.corporate",
        "EastmoneyBlockTradeSource",
        fetch_block_trades=[{"id": 1}],
    )
    _install(
        monkeypatch, "atst.sources.corporate", "EastmoneyUnlockSource", fetch_unlocks=[{"id": 2}]
    )
    _install(
        monkeypatch,
        "atst.sources.corporate",
        "EastmoneyPerformanceSource",
        fetch_performance=[{"id": 3}],
    )
    _install(
        monkeypatch, "atst.sources.corporate", "EastmoneyForecastSource", fetch_forecast=[{"id": 4}]
    )
    _install(monkeypatch, "atst.sources.corporate", "EastmoneyIpoSource", fetch_ipo=[{"id": 5}])

    assert CorporateSessionMixin.block_trades("600519", date="2026-11-24", size=5) == [{"id": 1}]
    assert CorporateSessionMixin.unlocks(
        "600519", begin="2026-01-01", end="2026-12-31", size=6
    ) == [{"id": 2}]
    assert CorporateSessionMixin.performance("600519", report_date="2026-06-30", size=7) == [
        {"id": 3}
    ]
    assert CorporateSessionMixin.forecast("600519", report_date="2026-06-30", size=8) == [{"id": 4}]
    assert CorporateSessionMixin.ipo_calendar(apply_date="2026-11-24", page=2, size=9) == [
        {"id": 5}
    ]

    block, unlock, perf, forecast, ipo = _Fake.instances
    assert block.calls == [
        ("fetch_block_trades", (), {"symbol": "600519", "date": "2026-11-24", "size": 5})
    ]
    assert unlock.calls == [
        (
            "fetch_unlocks",
            (),
            {"symbol": "600519", "begin": "2026-01-01", "end": "2026-12-31", "size": 6},
        )
    ]
    assert perf.calls == [
        ("fetch_performance", (), {"symbol": "600519", "report_date": "2026-06-30", "size": 7})
    ]
    assert forecast.calls == [
        ("fetch_forecast", (), {"symbol": "600519", "report_date": "2026-06-30", "size": 8})
    ]
    assert ipo.calls == [("fetch_ipo", (), {"apply_date": "2026-11-24", "page": 2, "size": 9})]
    assert all(inst.closed for inst in _Fake.instances)


def test_longhu_and_news_single_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch, "atst.sources.longhu", "EastmoneyTopListSource", fetch_lhb=[{"code": "600519"}]
    )
    _install(
        monkeypatch,
        "atst.sources.news",
        "SinaNewsSource",
        fetch_news=[{"title": "t", "datetime": "2026-11-24 10:00", "url": "u", "tag": ""}],
    )
    assert CorporateSessionMixin.longhu(date="2026-11-24", symbol="sh600519", page=2, size=5) == [
        {"code": "600519"}
    ]
    assert CorporateSessionMixin.news("600519", page=2, num=5, tag="研报", pages=1) == [
        {"title": "t", "datetime": "2026-11-24 10:00", "url": "u", "tag": ""}
    ]

    lhb, news = _Fake.instances
    assert lhb.calls == [
        ("fetch_lhb", (), {"date": "2026-11-24", "symbol": "sh600519", "page": 2, "size": 5})
    ]
    assert news.calls == [("fetch_news", ("600519",), {"page": 2, "num": 5, "tag": "研报"})]
    assert lhb.closed and news.closed


def test_news_multi_page_dedupes_and_stops_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    a = {"datetime": "2026-11-24 10:00", "title": "A", "url": "u1", "tag": ""}
    b = {"datetime": "2026-11-24 09:00", "title": "B", "url": "u2", "tag": ""}
    pages = {1: [a, b, a], 2: []}
    calls: list[int] = []

    def fetch_news(
        symbol: str, *, page: int = 1, num: int = 20, tag: str | None = None
    ) -> list[dict[str, Any]]:
        calls.append(page)
        return pages.get(page, [])

    _install(monkeypatch, "atst.sources.news", "SinaNewsSource", fetch_news=fetch_news)
    out = CorporateSessionMixin.news("600519", num=2, pages=3)
    assert out == [a, b]  # 跨页/页内去重
    assert calls == [1, 2]  # 第 2 页空 → 提前终止
    assert _Fake.instances[0].closed is True


def test_news_multi_page_stops_on_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    a = {"datetime": "2026-11-24 10:00", "title": "A", "url": "u1", "tag": ""}
    calls: list[int] = []

    def fetch_news(
        symbol: str, *, page: int = 1, num: int = 20, tag: str | None = None
    ) -> list[dict[str, Any]]:
        calls.append(page)
        return [a]

    _install(monkeypatch, "atst.sources.news", "SinaNewsSource", fetch_news=fetch_news)
    assert CorporateSessionMixin.news("600519", num=2, pages=3) == [a]
    assert calls == [1]  # 不足 num 条 → 不再翻页


def test_news_multi_page_full_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    def row(i: int) -> dict[str, Any]:
        return {"datetime": f"2026-11-24 0{i}:00", "title": f"T{i}", "url": f"u{i}", "tag": ""}

    pages = {1: [row(1), row(2)], 2: [row(3), row(4)], 3: [row(5), row(6)]}
    calls: list[int] = []

    def fetch_news(
        symbol: str, *, page: int = 1, num: int = 20, tag: str | None = None
    ) -> list[dict[str, Any]]:
        calls.append(page)
        return pages.get(page, [])

    _install(monkeypatch, "atst.sources.news", "SinaNewsSource", fetch_news=fetch_news)
    out = CorporateSessionMixin.news("600519", num=2, pages=3)
    assert [r["title"] for r in out] == ["T1", "T2", "T3", "T4", "T5", "T6"]
    assert calls == [1, 2, 3]


# --------------------------------------------------------------------------- #
# 资讯扩展（华尔街见闻 / 同花顺 / 巨潮）
# --------------------------------------------------------------------------- #
def test_breaking_news_theme_concept_and_hk_announcements(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.wallstreet.adapters",
        "WallstreetSource",
        fetch_breaking_news=[{"id": 1}],
    )
    _install(
        monkeypatch,
        "atst.sources.ths.adapters",
        "ThsSource",
        fetch_theme_attribution=[{"board": "白酒"}],
        fetch_concept_members=[{"code": "600519"}],
    )
    _install(
        monkeypatch,
        "atst.sources.cninfo.adapters",
        "CninfoSource",
        fetch_hk_announcements=[{"id": 2}],
    )

    assert CorporateSessionMixin.breaking_news(limit=3, channel="global-channel") == [{"id": 1}]
    # 空 symbol/date 翻译成 None（“全部/最新”）
    assert CorporateSessionMixin.theme_attribution("", date="", limit=5) == [{"board": "白酒"}]
    assert CorporateSessionMixin.concept_members("BK1", date="2026-11-24", limit=9) == [
        {"code": "600519"}
    ]
    assert CorporateSessionMixin.hk_announcements("", limit=10, start_date="", end_date="") == [
        {"id": 2}
    ]

    wallstreet, ths_theme, ths_concept, cninfo = _Fake.instances
    assert wallstreet.calls == [
        ("fetch_breaking_news", (), {"limit": 3, "channel": "global-channel"})
    ]
    assert ths_theme.calls == [("fetch_theme_attribution", (None,), {"date": None, "limit": 5})]
    assert ths_concept.calls == [
        ("fetch_concept_members", ("BK1",), {"date": "2026-11-24", "limit": 9})
    ]
    assert cninfo.calls == [
        (
            "fetch_hk_announcements",
            (None,),
            {"limit": 10, "start_date": None, "end_date": None},
        )
    ]
    assert all(inst.closed for inst in (wallstreet, ths_theme, ths_concept, cninfo))


# --------------------------------------------------------------------------- #
# datacenter 直查
# --------------------------------------------------------------------------- #
def test_dc_reports_and_query_filter_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    reports = CorporateSessionMixin.dc_reports()
    assert reports["board_attribution"] == "RPT_F10_CORETHEME_BOARDTYPE"

    _install(
        monkeypatch, "atst.sources.corporate", "EastmoneyDataCenterSource", fetch_rows=[{"r": 1}]
    )
    assert CorporateSessionMixin.dc_query("unknown_report", symbol="sh600519") == [{"r": 1}]
    assert CorporateSessionMixin.dc_query("ipo", symbol="sh600519") == [{"r": 1}]
    assert CorporateSessionMixin.dc_query("ipo", page=3, size=4) == [{"r": 1}]
    assert CorporateSessionMixin.dc_query(
        "performance", symbol="600519", filters=['REPORT_DATE="2026-06-30"'], page=2, size=5
    ) == [{"r": 1}]

    unknown, ipo, ipo_nosym, perf = _Fake.instances
    assert unknown.kwargs["report"] == "unknown_report"
    assert unknown.calls[0][2]["filters"] == ['SECURITY_CODE="600519"']
    assert ipo.kwargs["report"] == "ipo"
    assert ipo.calls[0][2]["filters"] == []  # ipo 无个股过滤键
    assert ipo_nosym.calls[0][2]["filters"] == []
    assert ipo_nosym.calls[0][2]["page"] == 3 and ipo_nosym.calls[0][2]["size"] == 4
    assert perf.calls[0][2]["filters"] == [
        'REPORT_DATE="2026-06-30"',
        'SECURITY_CODE="600519"',
    ]
    assert perf.calls[0][2]["page"] == 2 and perf.calls[0][2]["size"] == 5
    assert all(inst.closed for inst in _Fake.instances)


# --------------------------------------------------------------------------- #
# 失败与空负载
# --------------------------------------------------------------------------- #
def test_upstream_error_propagates_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "atst.sources.corporate",
        "EastmoneyProfileSource",
        fetch_profile=WebSourceError("profile upstream down", context={"source": "eastmoney"}),
    )
    with pytest.raises(WebSourceError, match="upstream down") as caught:
        CorporateSessionMixin.profile("600519")
    assert caught.value.code == "E7000"
    assert _Fake.instances[0].closed is True


def test_empty_payload_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "atst.sources.corporate", "EastmoneyNoticeSource", fetch_notices=[])
    _install(monkeypatch, "atst.sources.fundflow", "EastmoneyStockChangesSource", fetch_changes=[])
    assert CorporateSessionMixin.notices(["600519"]) == []
    assert FundFlowSessionMixin.stock_changes() == []
