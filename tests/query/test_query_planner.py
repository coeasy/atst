from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.query import QueryPlanner, QuerySpec


def test_default_provider_is_tdx_and_quotes_bind_quotation() -> None:
    plan = QueryPlanner().compile(
        QuerySpec.build("quotes", symbols=["600519"])
    )
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"
    assert plan.spec.symbols == ("sh600519",)


def test_tencent_minute_bars_bind_minute_kline_channel() -> None:
    plan = QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider="tencent",
            period="5min",
            count=100,
        )
    )
    assert plan.provider == "tencent"
    assert plan.channel == "minute_kline"


def test_provider_and_compat_source_conflict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryPlanner().compile(
            QuerySpec.build(
                "quotes",
                symbols=["sh600519"],
                provider="tdx",
                source="tencent",
            )
        )


def test_fingerprint_changes_with_provider_adjustment_and_window() -> None:
    planner = QueryPlanner()
    base = planner.compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider="eastmoney",
            period="day",
            count=100,
            adjustment="qfq",
        )
    ).fingerprint.value
    different_provider = planner.compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider="tencent",
            period="day",
            count=100,
            adjustment="qfq",
        )
    ).fingerprint.value
    different_adjust = planner.compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider="eastmoney",
            period="day",
            count=100,
            adjustment="hfq",
        )
    ).fingerprint.value
    different_count = planner.compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider="eastmoney",
            period="day",
            count=200,
            adjustment="qfq",
        )
    ).fingerprint.value

    assert len({base, different_provider, different_adjust, different_count}) == 4


def test_same_semantics_have_same_fingerprint_after_symbol_normalization() -> None:
    planner = QueryPlanner()
    left = planner.compile(
        QuerySpec.build("quotes", symbols=["600519"], provider="qq")
    )
    right = planner.compile(
        QuerySpec.build("quotes", symbols=["sh600519"], provider="tencent")
    )
    assert left.fingerprint.value == right.fingerprint.value


def test_bars_requires_single_symbol_and_positive_count() -> None:
    planner = QueryPlanner()
    with pytest.raises(ValidationError):
        planner.compile(
            QuerySpec.build(
                "bars",
                symbols=["600519", "000001"],
                provider="tdx",
                count=10,
            )
        )
    with pytest.raises(ValidationError):
        planner.compile(
            QuerySpec.build("bars", symbols=["600519"], provider="tdx", count=0)
        )
