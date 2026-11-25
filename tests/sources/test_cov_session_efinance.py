# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""efinance 对标扩展会话 Mixin 离线单测（覆盖率补强）。

重点覆盖三处真实本地逻辑：``stock_base_info`` 的逐只失败隔离、
``stock_report_dates`` 的去重/降序/条数钳制、``ipo_review`` 的字段回退映射；
其余期货/期权/债券入口验证参数透传与 ``finally close()``。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.errors import WebSourceError
from atst.sources._session_efinance import (
    DerivativeSessionMixin,
    FundMobSessionMixin,
    StockEfinanceMixin,
)
from atst.sources.corporate import VALID_REPORTS

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


def _install(monkeypatch: pytest.MonkeyPatch, attr: str, **results: Any) -> type[_Fake]:
    fake_cls = type(f"_Fake{attr}", (_Fake,), {"results": results})
    monkeypatch.setattr(f"atst.sources._session_efinance.{attr}", fake_cls)
    return fake_cls


@pytest.fixture(autouse=True)
def _clean_instances() -> Any:
    _Fake.instances.clear()
    yield
    _Fake.instances.clear()


@pytest.fixture(autouse=True)
def _shared(monkeypatch: pytest.MonkeyPatch) -> Any:
    sentinel = object()
    monkeypatch.setattr("atst.sources._session_efinance._shared_http", lambda: sentinel)
    return sentinel


# --------------------------------------------------------------------------- #
# 股票扩展
# --------------------------------------------------------------------------- #
def test_stock_base_info_isolates_single_failure(
    monkeypatch: pytest.MonkeyPatch, _shared: Any
) -> None:
    def fetch_profile(code: str) -> dict[str, Any]:
        if code == "bad":
            raise WebSourceError("profile upstream down", context={"source": "eastmoney"})
        return {"code": code}

    _install(monkeypatch, "EastmoneyProfileSource", fetch_profile=fetch_profile)
    rows = StockEfinanceMixin.stock_base_info(["600519", "bad", "000001"])
    assert rows == [{"code": "600519"}, {"code": "000001"}]
    inst = _Fake.instances[0]
    assert inst.client is _shared
    assert [call[1] for call in inst.calls] == [("600519",), ("bad",), ("000001",)]
    assert inst.closed is True


def test_stock_all_performance_forces_all_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "EastmoneyPerformanceSource", fetch_performance=[{"code": "600519"}])
    out = StockEfinanceMixin.stock_all_performance("2026-06-30")
    assert out == [{"code": "600519"}]
    inst = _Fake.instances[0]
    assert inst.calls == [
        ("fetch_performance", (), {"report_date": "2026-06-30", "all_pages": True})
    ]
    assert inst.closed is True


def test_stock_report_dates_dedupes_sorts_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"REPORTDATE": "2026-06-30"},
        {"REPORTDATE": "2025-12-31"},
        {"REPORTDATE": "2026-06-30"},
        {"REPORTDATE": None},
        {"REPORTDATE": ""},
        {"REPORTDATE": "2024-09-30"},
    ]
    _install(monkeypatch, "EastmoneyDataCenterSource", fetch_rows=rows)
    dates = StockEfinanceMixin.stock_report_dates()
    assert dates == ["2026-06-30", "2025-12-31", "2024-09-30"]

    inst = _Fake.instances[0]
    assert inst.calls == [
        (
            "fetch_rows",
            (),
            {
                "sort_columns": "REPORTDATE",
                "sort_types": "-1",
                "page": 1,
                "size": 100,
                "report": VALID_REPORTS["performance"],
            },
        )
    ]
    assert inst.closed is True

    # 下限 1 / 上限 500
    StockEfinanceMixin.stock_report_dates(limit=0)
    assert _Fake.instances[1].calls[0][2]["size"] == 1
    StockEfinanceMixin.stock_report_dates(limit=9999)
    assert _Fake.instances[2].calls[0][2]["size"] == 500


def test_ipo_review_maps_fields_with_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "SECURITY_CODE": "001",
            "SECURITY_NAME": "甲公司",
            "ORG_NAME": "保荐人A",
            "SPONSOR_NAME": "券商A",
            "AUDIT_STATE": "通过",
            "UPDATE_DATE": "2026-01-01",
        },
        {
            "NAME": "乙公司",
            "ISSUER": "发行人B",
            "SPONSOR": "券商B",
            "STATE": "问询",
            "NOTICE_DATE": "2026-02-02",
        },
        "not-a-dict",
        {},
    ]
    _install(monkeypatch, "EastmoneyDataCenterSource", fetch_rows=rows)
    out = StockEfinanceMixin.ipo_review(page=2, size=10)
    assert out == [
        {
            "code": "001",
            "name": "甲公司",
            "issuer": "保荐人A",
            "sponsor": "券商A",
            "audit_state": "通过",
            "update_date": "2026-01-01",
        },
        {
            "code": "",
            "name": "乙公司",
            "issuer": "发行人B",
            "sponsor": "券商B",
            "audit_state": "问询",
            "update_date": "2026-02-02",
        },
        {
            "code": "",
            "name": "",
            "issuer": "",
            "sponsor": "",
            "audit_state": "",
            "update_date": "",
        },
    ]
    inst = _Fake.instances[0]
    assert inst.calls == [
        (
            "fetch_rows",
            (),
            {"page": 2, "size": 10, "sort_columns": "UPDATE_DATE", "sort_types": "-1"},
        )
    ]
    assert inst.closed is True


# --------------------------------------------------------------------------- #
# 基金扩展
# --------------------------------------------------------------------------- #
def test_fund_base_info_multi_isolates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch_base_info(code: str) -> dict[str, Any]:
        if code == "bad":
            raise WebSourceError("fund upstream down")
        return {"code": code}

    _install(monkeypatch, "FundMobSource", fetch_base_info=fetch_base_info)
    assert FundMobSessionMixin.fund_base_info("161725") == {"code": "161725"}
    assert FundMobSessionMixin.fund_base_info_multi(["161725", "bad", "161726"]) == [
        {"code": "161725"},
        {"code": "161726"},
    ]
    assert all(inst.closed for inst in _Fake.instances)


def test_fund_detail_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "FundMobSource",
        fetch_manager={"manager": "王平"},
        fetch_holdings=[{"code": "600519"}],
        fetch_period_change=[{"period": "1y"}],
        fetch_asset_allocation=[{"stock": 85.3}],
        fetch_industry_distribution=[{"industry": "制造"}],
        fetch_public_dates=["2026-06-30"],
    )
    assert FundMobSessionMixin.fund_manager("161725") == {"manager": "王平"}
    assert FundMobSessionMixin.fund_holdings("161725", dates=["2026-06-30"]) == [{"code": "600519"}]
    assert FundMobSessionMixin.fund_period_change("161725") == [{"period": "1y"}]
    assert FundMobSessionMixin.fund_asset_allocation("161725", dates="2026-06-30") == [
        {"stock": 85.3}
    ]
    assert FundMobSessionMixin.fund_industry_distribution("161725") == [{"industry": "制造"}]
    assert FundMobSessionMixin.fund_public_dates("161725") == ["2026-06-30"]

    holdings = _Fake.instances[1]
    assert holdings.calls == [("fetch_holdings", ("161725",), {"dates": ["2026-06-30"]})]
    assert all(inst.closed for inst in _Fake.instances)


# --------------------------------------------------------------------------- #
# 期货 / 期权 / 债券
# --------------------------------------------------------------------------- #
def test_futures_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "EastmoneyFuturesSource",
        fetch_base_info=[{"code": "rb2601"}],
        fetch_realtime={"code": "rb2601"},
        fetch_kline=[{"close": 1}],
        fetch_deal_detail=[{"price": 1}],
    )
    assert DerivativeSessionMixin.futures_base_info() == [{"code": "rb2601"}]
    assert DerivativeSessionMixin.futures_realtime("rb2601") == {"code": "rb2601"}
    assert DerivativeSessionMixin.futures_kline("rb2601", period="week", count=5, adjust="qfq") == [
        {"close": 1}
    ]
    assert DerivativeSessionMixin.futures_trades("rb2601", max_count=50) == [{"price": 1}]

    kline = _Fake.instances[2]
    assert kline.calls == [
        ("fetch_kline", ("rb2601",), {"period": "week", "count": 5, "adjust": "qfq"})
    ]
    trades = _Fake.instances[3]
    assert trades.calls == [("fetch_deal_detail", ("rb2601",), {"max_count": 50})]
    assert all(inst.closed for inst in _Fake.instances)


def test_options_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "EastmoneyOptionsSource",
        fetch_contract_list=[{"code": "10008888"}],
        fetch_snapshot={"code": "10008888"},
        fetch_trends=[],
    )
    assert DerivativeSessionMixin.options_list(market="10", size=20, page=2) == [
        {"code": "10008888"}
    ]
    assert DerivativeSessionMixin.options_snapshot("10008888") == {"code": "10008888"}
    assert DerivativeSessionMixin.options_trends("10008888", ndays=5) == []

    listing = _Fake.instances[0]
    assert listing.calls == [("fetch_contract_list", (), {"market": "10", "size": 20, "page": 2})]
    trends = _Fake.instances[2]
    assert trends.calls == [("fetch_trends", ("10008888",), {"ndays": 5})]
    assert all(inst.closed for inst in _Fake.instances)


def test_bond_methods_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "EastmoneyBondSource",
        fetch_realtime=[{"code": "113001"}],
        fetch_base_info=[{"code": "113001"}],
        fetch_all_base_info=[{"code": "113001"}],
        fetch_kline=[{"close": 1}],
        fetch_history_bill=[{"net": 1}],
        fetch_today_bill=None,
        fetch_deal_detail=[{"price": 1}],
    )
    assert DerivativeSessionMixin.bond_realtime(["113001"]) == [{"code": "113001"}]
    assert DerivativeSessionMixin.bond_base_info(["113001"]) == [{"code": "113001"}]
    assert DerivativeSessionMixin.bond_all_base_info() == [{"code": "113001"}]
    assert DerivativeSessionMixin.bond_kline("113001", period="week", count=3, adjust="hfq") == [
        {"close": 1}
    ]
    assert DerivativeSessionMixin.bond_history_bill("113001", count=5) == [{"net": 1}]
    assert DerivativeSessionMixin.bond_today_bill("113001") is None
    assert DerivativeSessionMixin.bond_trades("113001", max_count=8) == [{"price": 1}]

    kline = _Fake.instances[3]
    assert kline.calls == [
        ("fetch_kline", ("113001",), {"period": "week", "count": 3, "adjust": "hfq"})
    ]
    bill = _Fake.instances[4]
    assert bill.calls == [("fetch_history_bill", ("113001",), {"count": 5})]
    trades = _Fake.instances[6]
    assert trades.calls == [("fetch_deal_detail", ("113001",), {"max_count": 8})]
    assert all(inst.closed for inst in _Fake.instances)


def test_upstream_error_propagates_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        "EastmoneyFuturesSource",
        fetch_realtime=WebSourceError("futures upstream down", context={"source": "eastmoney"}),
    )
    with pytest.raises(WebSourceError, match="upstream down") as caught:
        DerivativeSessionMixin.futures_realtime("rb2601")
    assert caught.value.code == "E7000"
    assert _Fake.instances[0].closed is True


def test_empty_payload_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, "EastmoneyFuturesSource", fetch_base_info=[])
    _install(monkeypatch, "EastmoneyOptionsSource", fetch_trends=[])
    _install(monkeypatch, "EastmoneyBondSource", fetch_today_bill=None)
    assert DerivativeSessionMixin.futures_base_info() == []
    assert DerivativeSessionMixin.options_trends("10008888") == []
    assert DerivativeSessionMixin.bond_today_bill("113001") is None
