# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3-2 低覆盖模块补离线单测：HoldersSessionMixin / SectorMacroSessionMixin /
EsgChipSessionMixin（sources/_session_holders.py、_session_sector_macro.py、
_session_esg_chip.py）。

全部离线：monkeypatch 各域后端 Source 类，验证 Mixin 的公开行为——
过滤条件拼装、报表名选择、排序参数透传、``close()`` 生命周期——
不发起任何真实 HTTP 请求，不测实现细节。
"""

from __future__ import annotations

from typing import Any

import pytest

from atst.sources._session_esg_chip import EsgChipSessionMixin
from atst.sources._session_holders import HoldersSessionMixin
from atst.sources._session_sector_macro import SectorMacroSessionMixin

pytestmark = pytest.mark.unit


class _FakeRowsSource:
    """通用假后端：记录构造参数与 fetch_rows 调用，返回罐头行。"""

    last_instance: _FakeRowsSource | None = None

    def __init__(self, *, report: str | None = None, client: Any = None, **kwargs: Any) -> None:
        self.report = report
        self.client = client
        self.extra_kwargs = kwargs
        self.fetch_kwargs: dict[str, Any] | None = None
        self.closed = False
        _FakeRowsSource.last_instance = self

    def fetch_rows(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fetch_kwargs = kwargs
        return [{"report": self.report, "ok": True}]

    def close(self) -> None:
        self.closed = True


class TestHoldersMixin:
    """北向持股 / 十大股东 / 解禁 / 业绩预告（旧版）。"""

    def _fetch_kwargs(self) -> dict[str, Any]:
        inst = _FakeRowsSource.last_instance
        assert inst is not None and inst.closed, "后端实例必须被 try/finally close()"
        return inst.fetch_kwargs or {}

    def test_northbound_hold_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#1 北向持股：symbol/hold_date/mutual_type 全部转成报表过滤条件。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        rows = HoldersSessionMixin.northbound_hold(
            symbol="sh600519", hold_date="2026-06-30", mutual_type="001", size=10, page=2
        )
        assert rows == [{"report": "northbound_hold", "ok": True}]
        kw = self._fetch_kwargs()
        assert kw["filters"] == [
            'SECURITY_CODE="600519"',
            'HOLD_DATE="2026-06-30"',
            'MUTUAL_TYPE="001"',
        ]
        assert kw["sort_columns"] == "HOLD_DATE"
        assert kw["sort_types"] == "-1"
        assert kw["page"] == 2
        assert kw["size"] == 10

    def test_northbound_hold_market_wide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#2 北向持股：无参数=全市场，filters 为空。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        HoldersSessionMixin.northbound_hold()
        assert self._fetch_kwargs()["filters"] == []

    def test_top_holders_secucode_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#3 十大股东：以 SECUCODE（600519.SH）过滤，后缀随市场前缀。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        HoldersSessionMixin.top_holders(symbol="sh600519", end_date="2026-06-30")
        kw = self._fetch_kwargs()
        assert 'SECUCODE="600519.SH"' in kw["filters"]
        assert 'END_DATE="2026-06-30"' in kw["filters"]
        assert kw["sort_columns"] == "HOLDER_RANK"

    def test_top_holders_bj_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#4 十大股东：bj 前缀 → .BJ 后缀；未识别后缀回落 SZ。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        HoldersSessionMixin.top_holders(symbol="bj830799")
        assert 'SECUCODE="830799.BJ"' in self._fetch_kwargs()["filters"]
        HoldersSessionMixin.top_holders(symbol="us999999")
        assert 'SECUCODE="999999.SZ"' in self._fetch_kwargs()["filters"]

    def test_unlock_stocks_date_range(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#5 解禁股票：begin/end 转成 FREE_DATE 区间过滤。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        HoldersSessionMixin.unlock_stocks(symbol="sz000001", begin="2026-01-01", end="2026-12-31")
        kw = self._fetch_kwargs()
        assert kw["filters"] == [
            'SECURITY_CODE="000001"',
            "FREE_DATE>='2026-01-01'",
            "FREE_DATE<='2026-12-31'",
        ]

    def test_earnings_preview_reportdate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#6 业绩预告旧版：report_date 无时刻时补 00:00:00，已带时刻则原样。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        HoldersSessionMixin.earnings_preview(symbol="600519", report_date="2026-06-30")
        assert 'REPORTDATE="2026-06-30 00:00:00"' in self._fetch_kwargs()["filters"]
        HoldersSessionMixin.earnings_preview(symbol="600519", report_date="2026-06-30 00:00:00")
        kw = self._fetch_kwargs()
        assert kw["filters"] == [
            'SECURITY_CODE="600519"',
            'REPORTDATE="2026-06-30 00:00:00"',
        ]


class TestSectorMacroMixin:
    """行业指数 / 概念成分 / CPI / PPI / GDP / 可转债。"""

    def _fetch_kwargs(self) -> dict[str, Any]:
        inst = _FakeRowsSource.last_instance
        assert inst is not None and inst.closed
        return inst.fetch_kwargs or {}

    def test_industry_index_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#7 行业指数：report_date/board_code 过滤与缺省全行业。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        SectorMacroSessionMixin.industry_index(
            report_date="2026-09-10", board_code="901001", size=5, page=3
        )
        kw = self._fetch_kwargs()
        assert kw["filters"] == ['REPORT_DATE="2026-09-10"', 'BOARD_CODE="901001"']
        assert kw["sort_columns"] == "CHANGE_RATE"
        assert kw["page"] == 3 and kw["size"] == 5

    def test_concept_index_sort_fixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#8 概念指数成分：INDEX_CODE 过滤 + 固定升序排序。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        SectorMacroSessionMixin.concept_index(index_code="BK1027")
        kw = self._fetch_kwargs()
        assert kw["filters"] == ['INDEX_CODE="BK1027"']
        assert kw["sort_columns"] == "INDEX_CODE"
        assert kw["sort_types"] == "1"

    def test_macro_cpi_ppi_gdp_reports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#9 CPI/PPI/GDP：报表名与排序键各归各位。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        assert SectorMacroSessionMixin.macro_cpi(size=7) == [{"report": "macro_cpi", "ok": True}]
        kw = self._fetch_kwargs()
        assert kw["sort_columns"] == "REPORT_DATE" and kw["size"] == 7
        SectorMacroSessionMixin.macro_ppi()
        assert _FakeRowsSource.last_instance is not None
        assert _FakeRowsSource.last_instance.report == "macro_ppi"
        SectorMacroSessionMixin.macro_gdp(page=4)
        assert self._fetch_kwargs()["page"] == 4
        assert _FakeRowsSource.last_instance is not None
        assert _FakeRowsSource.last_instance.report == "macro_gdp"

    def test_convertible_bonds_passthrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#10 可转债列表：sort_columns 透传给后端。"""
        monkeypatch.setattr("atst.sources.corporate.EastmoneyDataCenterSource", _FakeRowsSource)
        SectorMacroSessionMixin.convertible_bonds(sort_columns="RATING")
        kw = self._fetch_kwargs()
        assert kw["sort_columns"] == "RATING"
        assert _FakeRowsSource.last_instance is not None
        assert _FakeRowsSource.last_instance.report == "convertible_bonds"


class _FakeEsgSource:
    """假 ESG 后端：记录构造与方法调用。"""

    last_instance: _FakeEsgSource | None = None
    canned: Any = {"agencies": []}

    def __init__(self, *, client: Any = None, **kwargs: Any) -> None:
        self.client = client
        self.closed = False
        self.calls: dict[str, Any] = {}
        _FakeEsgSource.last_instance = self

    def close(self) -> None:
        self.closed = True


class TestEsgChipMixin:
    """ESG 评级 / 筹码分布。"""

    def test_esg_rating(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#11 个股 ESG 评级：透传 fetch_stock_info 并关闭后端。"""
        src_cls = type(
            "_FakeStockInfo",
            (_FakeEsgSource,),
            {"fetch_stock_info": lambda self, s: {"agencies": [{"esg_score": 61.9}], "symbol": s}},
        )
        monkeypatch.setattr("atst.sources.esg.SinaEsgStockInfoSource", src_cls)
        out = EsgChipSessionMixin.esg_rating("600519")
        assert out == {"agencies": [{"esg_score": 61.9}], "symbol": "600519"}
        assert _FakeEsgSource.last_instance is not None
        assert _FakeEsgSource.last_instance.closed

    def test_esg_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#12 ESG 评级历史：透传 fetch_history。"""
        src_cls = type(
            "_FakeHistory",
            (_FakeEsgSource,),
            {"fetch_history": lambda self, s: {"agencies": [{"type": s}]}},
        )
        monkeypatch.setattr("atst.sources.esg.SinaEsgHistorySource", src_cls)
        assert EsgChipSessionMixin.esg_history("000001") == {"agencies": [{"type": "000001"}]}
        assert _FakeEsgSource.last_instance is not None
        assert _FakeEsgSource.last_instance.closed

    def test_esg_ratings_all_msci(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#13 全市场 ESG 列表（缺省 msci）：rating 参数按 MSCI 关键字透传。"""
        captured: dict[str, Any] = {}

        class _FakeMsci(_FakeEsgSource):
            def fetch_ratings(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"rows": 1}

        monkeypatch.setattr("atst.sources.esg.SinaEsgMsciSource", _FakeMsci)
        out = EsgChipSessionMixin.esg_ratings_all(market="CN", rating="AA", sort_column="esg_dt")
        assert out == {"rows": 1}
        assert captured == {
            "market": "CN",
            "rating": "AA",
            "sort_column": "esg_dt",
            "sort_order": "desc",
        }

    def test_esg_ratings_all_hz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#14 全市场 ESG 列表（华证）：rating 映射为 grade 关键字。"""
        captured: dict[str, Any] = {}

        class _FakeHz(_FakeEsgSource):
            def fetch_ratings(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"rows": 2}

        monkeypatch.setattr("atst.sources.esg.SinaEsgHzSource", _FakeHz)
        out = EsgChipSessionMixin.esg_ratings_all(source="hz", market="cn", rating="AAA")
        assert out == {"rows": 2}
        assert captured["grade"] == "AAA"
        assert captured["market"] == "cn"
        assert "rating" not in captured

    def test_chip_distribution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#15 个股筹码分布：days 透传，后端关闭。"""
        captured: dict[str, Any] = {}

        class _FakeChip(_FakeEsgSource):
            def fetch_chip_distribution(self, symbol: str, *, days: int = 5) -> dict[str, Any]:
                captured["symbol"] = symbol
                captured["days"] = days
                return {"symbol": symbol}

        monkeypatch.setattr("atst.sources.chip.EastmoneyChipDistributionSource", _FakeChip)
        assert EsgChipSessionMixin.chip_distribution("sh600519", days=10) == {"symbol": "sh600519"}
        assert captured == {"symbol": "sh600519", "days": 10}
        assert _FakeEsgSource.last_instance is not None
        assert _FakeEsgSource.last_instance.closed

    def test_chip_distributions_batch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#16 批量筹码分布：symbols/days/max_count 透传。"""
        captured: dict[str, Any] = {}

        class _FakeChip(_FakeEsgSource):
            def fetch_chip_distributions(
                self, symbols: Any, *, days: int = 5, max_count: int = 20
            ) -> list[dict[str, Any]]:
                captured.update(symbols=list(symbols), days=days, max_count=max_count)
                return [{"symbol": s} for s in symbols]

        monkeypatch.setattr("atst.sources.chip.EastmoneyChipDistributionSource", _FakeChip)
        out = EsgChipSessionMixin.chip_distributions(["600519", "000001"], days=3, max_count=2)
        assert out == [{"symbol": "600519"}, {"symbol": "000001"}]
        assert captured == {"symbols": ["600519", "000001"], "days": 3, "max_count": 2}
