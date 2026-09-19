from __future__ import annotations

import dataclasses

import pytest

from tstdx.errors import ValidationError
from tstdx.providers import resolve_provider
from tstdx.query import QueryPlanner, QuerySpec


def test_default_provider_is_tdx_and_quotes_bind_quotation() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols=["600519"]))
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"
    assert plan.spec.symbols == ("sh600519",)


def test_configured_planner_default_provider_is_used_when_query_is_unspecified() -> None:
    plan = QueryPlanner(default_provider="tencent").compile(
        QuerySpec.build("quotes", symbols=["600519"])
    )
    assert plan.provider == "tencent"
    assert plan.channel == "quote"


def test_explicit_query_provider_overrides_planner_default() -> None:
    plan = QueryPlanner(default_provider="tencent").compile(
        QuerySpec.build("quotes", symbols=["600519"], provider="sina")
    )
    assert plan.provider == "sina"
    assert plan.channel == "quote"


def test_empty_default_provider_is_rejected_not_silently_replaced() -> None:
    with pytest.raises(ValidationError, match="default_provider"):
        QueryPlanner(default_provider="")


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
    # v13 SSOT：QuerySpec 不再持有 ``source`` 字段；provider/source 选择器冲突
    # 在 provider 解析边界统一 fail-fast（见 tstdx.providers.resolve_provider）。
    with pytest.raises(ValidationError):
        resolve_provider(provider="tdx", source="tencent")


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
    left = planner.compile(QuerySpec.build("quotes", symbols=["600519"], provider="qq"))
    right = planner.compile(QuerySpec.build("quotes", symbols=["sh600519"], provider="tencent"))
    assert left.fingerprint.value == right.fingerprint.value


def test_default_bars_period_is_canonical_day() -> None:
    planner = QueryPlanner()
    implicit = planner.compile(
        QuerySpec.build("bars", symbols=["600519"], provider="tdx", count=100, period="")
    )
    explicit = planner.compile(
        QuerySpec.build("bars", symbols=["sh600519"], provider="tdx", count=100, period="day")
    )
    assert implicit.spec.period == "day"
    assert implicit.fingerprint.value == explicit.fingerprint.value


def test_query_spec_has_no_max_age_knob() -> None:
    """``max_age`` 是缓存层的新鲜度上界，直连执行面没有它可作用的对象，故整体删除。

    删除前它挂在 Client / CLI / HTTP / WS / MCP 五个入口上，却没有任何执行期消费者：
    调用方设置它只会得到"已经生效"的错觉。这里同时钉住它不许回来。
    """
    assert "max_age" not in {field.name for field in dataclasses.fields(QuerySpec)}
    with pytest.raises(TypeError):
        QuerySpec.build("quotes", symbols=["600519"], provider="tencent", max_age=0)


def test_deadline_ms_changes_execution_budget_not_data_identity() -> None:
    """``max_age`` 退场后，唯一合法的"策略不进身份"实例是 ``deadline_ms``。"""
    planner = QueryPlanner()
    tight = planner.compile(
        QuerySpec.build("quotes", symbols=["600519"], provider="tencent", deadline_ms=1000)
    )
    loose = planner.compile(
        QuerySpec.build("quotes", symbols=["600519"], provider="tencent", deadline_ms=9000)
    )

    assert (tight.deadline_ms, loose.deadline_ms) == (1000, 9000)
    assert tight.fingerprint.value == loose.fingerprint.value
    assert '"deadline_ms"' not in tight.fingerprint.canonical


def test_allow_stale_is_rejected_because_the_path_is_direct() -> None:
    """不是"尚未实现"，而是直连口径下永远没有可容忍的过期副本。"""
    with pytest.raises(ValidationError, match="currentness"):
        QueryPlanner().compile(
            QuerySpec.build(
                "quotes",
                symbols=["600519"],
                provider="tencent",
                allow_stale=True,
            )
        )


def test_allow_partial_is_limited_to_quotes() -> None:
    with pytest.raises(ValidationError, match="quotes BatchResult"):
        QueryPlanner().compile(
            QuerySpec.build(
                "bars",
                symbols=["600519"],
                provider="tdx",
                count=10,
                allow_partial=True,
            )
        )


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
        planner.compile(QuerySpec.build("bars", symbols=["600519"], provider="tdx", count=0))


def test_zero_deadline_is_not_silently_defaulted() -> None:
    with pytest.raises(ValidationError):
        QueryPlanner().compile(
            QuerySpec.build(
                "quotes",
                symbols=["600519"],
                provider="tencent",
                deadline_ms=0,
            )
        )
