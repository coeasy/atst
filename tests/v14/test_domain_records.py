from __future__ import annotations

from atst.domain.records import (
    BondRecord,
    FinancialRecord,
    FundRecord,
    MacroRecord,
    MarketDataRecord,
    NewsRecord,
    OptionRecord,
    ResearchRecord,
    SearchRecord,
    as_record_list,
    normalize_to_records,
    record_to_dicts,
)
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.typed_query import (
    FundManagerQuery,
    record_type_for,
    records_from_data,
    records_from_response,
)


class TestRecordRoundTrip:
    def test_financial_record_roundtrip(self) -> None:
        original = FinancialRecord(
            code="600519.SH",
            report_date="2026-06-30",
            metrics={"revenue": 100.5, "net_profit": 50.2},
        )
        data = original.to_dict()
        restored = FinancialRecord.from_dict(data)
        assert restored.code == original.code
        assert restored.report_date == original.report_date
        assert restored.metrics["revenue"] == 100.5
        assert restored.metrics["net_profit"] == 50.2

    def test_fund_record_roundtrip(self) -> None:
        original = FundRecord(
            code="110011", name="易方达优质精选", fund_type=1, metrics={"nav": 2.5}
        )
        data = original.to_dict()
        restored = FundRecord.from_dict(data)
        assert restored.name == "易方达优质精选"
        assert restored.fund_type == 1
        assert restored.metrics["nav"] == 2.5

    def test_bond_record_roundtrip(self) -> None:
        original = BondRecord(
            code="113001.SH",
            name="中行转债",
            bond_type="convertible",
            price=118.5,
            metrics={"premium": -2.3},
        )
        data = original.to_dict()
        restored = BondRecord.from_dict(data)
        assert restored.bond_type == "convertible"
        assert restored.price == 118.5
        assert restored.metrics["premium"] == -2.3

    def test_news_record_roundtrip(self) -> None:
        original = NewsRecord(
            id="n1",
            title="测试新闻",
            source="eastmoney",
            time="2026-09-13 10:00:00",
            url="https://example.com",
            symbol="600519.SH",
        )
        data = original.to_dict()
        restored = NewsRecord.from_dict(data)
        assert restored.title == "测试新闻"
        assert restored.symbol == "600519.SH"

    def test_research_record_roundtrip(self) -> None:
        original = ResearchRecord(
            code="600519.SH",
            title="深度研究",
            org="中信证券",
            analyst="张三",
            rating="买入",
            date="2026-09-13",
            target_price=2000.0,
        )
        data = original.to_dict()
        restored = ResearchRecord.from_dict(data)
        assert restored.rating == "买入"
        assert restored.target_price == 2000.0

    def test_option_record_roundtrip(self) -> None:
        original = OptionRecord(
            code="10000001",
            name="茅台购9月",
            option_type="call",
            strike=1800.0,
            expiry="2026-09-24",
            price=20.5,
            metrics={"delta": 0.5, "gamma": 0.01},
        )
        data = original.to_dict()
        restored = OptionRecord.from_dict(data)
        assert restored.option_type == "call"
        assert restored.strike == 1800.0
        assert restored.metrics["delta"] == 0.5

    def test_macro_record_roundtrip(self) -> None:
        original = MacroRecord(kind="fx_rates", key="USDCNY", name="美元人民币", value=7.13)
        data = original.to_dict()
        restored = MacroRecord.from_dict(data)
        assert restored.value == 7.13
        assert restored.key == "USDCNY"

    def test_none_fields_stripped_in_output(self) -> None:
        """保证输出与历史 list[dict] 兼容：None 字段不出现。"""
        record = ResearchRecord(code="600519.SH", title="研报")
        data = record.to_dict()
        assert "target_price" not in data  # None 字段剥离
        assert data["code"] == "600519.SH"
        assert data["title"] == "研报"


class TestRecordHelpers:
    def test_as_record_list_batch(self) -> None:
        items = [{"code": "a", "name": "A", "fund_type": 1}, {"code": "b", "name": "B"}]
        records = as_record_list(items, FundRecord)
        assert len(records) == 2
        assert all(isinstance(r, FundRecord) for r in records)
        assert records[0].code == "a"

    def test_record_to_dicts_reverse(self) -> None:
        records = [FundRecord(code="a", name="A"), FundRecord(code="b", name="B")]
        data = record_to_dicts(records)
        assert data[0]["code"] == "a"
        assert data[1]["name"] == "B"

    def test_normalize_to_records_variants(self) -> None:
        assert len(normalize_to_records({"code": "x"}, FundRecord)) == 1
        assert len(normalize_to_records([{"code": "x"}, {"code": "y"}], FundRecord)) == 2
        assert len(normalize_to_records(FundRecord(code="z"), FundRecord)) == 1


class TestRecordQueryIntegration:
    def test_record_type_for_maps_capability(self) -> None:
        assert record_type_for("fund_manager") is FundRecord
        assert record_type_for("balance_sheet") is FinancialRecord
        assert record_type_for("bond_trades") is BondRecord
        assert record_type_for("news_financial") is NewsRecord
        assert record_type_for("research_reports") is ResearchRecord
        assert record_type_for("options_list") is OptionRecord
        assert record_type_for("hot_rank") is MarketDataRecord
        assert record_type_for("wencai") is SearchRecord
        assert record_type_for("fx_rates") is MacroRecord
        assert record_type_for("unknown_cap") is None

    def test_records_from_data_normalizes(self) -> None:
        records = records_from_data("fund_manager", {"code": "000001", "name": "基金A"})
        assert isinstance(records[0], FundRecord)
        assert records[0].code == "000001"

    def test_records_from_data_unregistered_passthrough(self) -> None:
        raw = {"a": 1}
        records = records_from_data("unknown_cap", raw)
        assert records == [raw]

    def _fund_manager_result(self, data: object) -> QueryResult:
        plan = QueryPlanner().compile(
            QuerySpec.build(
                "fund_manager",
                provider="eastmoney",
                options={"args": [], "kwargs": {"code": "000001"}},
            )
        )
        return QueryResult.from_plan(data, plan=plan, provenance=Provenance.direct(plan))

    def test_records_from_response_success_path(self) -> None:
        response = self._fund_manager_result(
            {
                "code": "000001",
                "name": "测试基金",
                "fund_type": 1,
                "nav": 2.5,
                "acc_nav": 3.1,
            }
        )
        query = FundManagerQuery(code="000001")

        records = records_from_response(query, response)

        assert len(records) == 1
        assert isinstance(records[0], FundRecord)
        assert records[0].code == "000001"
        assert records[0].metrics["nav"] == 2.5

    def test_records_from_response_empty_result_returns_empty(self) -> None:
        """v13 内核世界里执行失败即异常，None payload 归一化为空记录。"""
        response = self._fund_manager_result(None)
        query = FundManagerQuery(code="000001")

        assert records_from_response(query, response) == []
        assert records_from_response(query, None) == []
