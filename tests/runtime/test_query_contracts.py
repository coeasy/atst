from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

import tstdx
from tstdx.direct_provider import DIRECT_BINDINGS
from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, ProvenanceKind, QueryResult


def test_registry_has_one_default_and_local_is_not_tdx() -> None:
    assert PROVIDERS.default_provider == "tdx"
    assert set(PROVIDERS.ids()) == {
        "tdx",
        "local_vipdoc",
        "tencent",
        "sina",
        "eastmoney",
        "baidu",
    }
    assert not any(channel.local for channel in PROVIDERS.get("tdx").channels)
    local = PROVIDERS.get("local_vipdoc").channel("vipdoc")
    assert local.local is True
    assert local.live is False


def test_registry_is_executable_truth() -> None:
    declared = {
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in channel.capabilities
    }
    bound = {item.key for item in DIRECT_BINDINGS}
    assert declared == bound


def test_query_spec_has_no_legacy_source_or_partial_semantics() -> None:
    names = {item.name for item in fields(QuerySpec)}
    assert "source" not in names
    assert "allow_partial" not in names
    with pytest.raises(TypeError):
        QuerySpec.build("quotes", symbols="sh600519", source="tencent")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        QuerySpec.build("quotes", symbols="sh600519", allow_partial=True)  # type: ignore[call-arg]


def test_public_runtime_contracts_are_lazy_exported() -> None:
    assert tstdx.QuerySpec is QuerySpec
    assert tstdx.QueryPlanner is QueryPlanner
    assert tstdx.PROVIDERS is PROVIDERS
    assert tstdx.QueryResult is QueryResult


def test_default_quotes_plan_is_tdx_quotation() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols=["600519.SH"]))
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"
    assert plan.spec.symbols == ("sh600519",)
    assert plan.batch_limit == 60
    assert plan.local_channel is False
    assert plan.fingerprint.value.startswith("q2:")


def test_tdx_tier_a_is_fully_plannable() -> None:
    planner = QueryPlanner()
    symbol_caps = ("quotes", "bars", "snapshot", "minute", "trades")
    for capability in symbol_caps:
        kwargs = {"symbols": "sh600519", "provider": "tdx"}
        if capability == "bars":
            kwargs.update(period="day", count=20)
        plan = planner.compile(QuerySpec.build(capability, **kwargs))
        assert plan.provider == "tdx"
        assert plan.channel == "quotation"
        assert plan.spec.capability == capability

    for capability in ("security_count", "security_list"):
        plan = planner.compile(QuerySpec.build(capability, provider="tdx", market=0))
        assert plan.channel == "quotation"


def test_equivalent_bar_period_aliases_share_fingerprint() -> None:
    planner = QueryPlanner()
    first = planner.compile(QuerySpec.build("bars", symbols="sh600519", period="daily", count=20))
    second = planner.compile(QuerySpec.build("bars", symbols="600519.SH", period="d", count=20))
    assert first.spec.period == second.spec.period == "day"
    assert first.fingerprint == second.fingerprint


def test_market_is_part_of_query_identity() -> None:
    planner = QueryPlanner()
    first = planner.compile(QuerySpec.build("security_list", provider="tdx", market=0))
    second = planner.compile(QuerySpec.build("security_list", provider="tdx", market=1))
    assert first.fingerprint != second.fingerprint


def test_options_order_does_not_change_query_identity() -> None:
    planner = QueryPlanner()
    first = planner.compile(
        QuerySpec.build("quotes", symbols="sh600519", options={"b": 2, "a": 1})
    )
    second = planner.compile(
        QuerySpec.build("quotes", symbols="sh600519", options={"a": 1, "b": 2})
    )
    assert first.fingerprint == second.fingerprint


def test_ambiguous_web_provider_is_rejected_before_io() -> None:
    with pytest.raises(ValidationError, match="不是 Provider"):
        QueryPlanner().compile(
            QuerySpec.build("quotes", symbols="sh600519", provider="web")
        )


def test_unpromoted_capability_fails_before_io() -> None:
    with pytest.raises(ValidationError, match="不支持"):
        QueryPlanner().compile(QuerySpec.build("options_list", provider="eastmoney"))


def test_local_vipdoc_is_explicit_historical_provider() -> None:
    plan = QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider="local_vipdoc",
            period="day",
            count=20,
        )
    )
    assert plan.provider == "local_vipdoc"
    assert plan.channel == "vipdoc"
    assert plan.local_channel is True

    with pytest.raises(ValidationError, match="不是 live channel"):
        QueryPlanner().compile(
            QuerySpec.build(
                "bars",
                symbols="sh600519",
                provider="local_vipdoc",
                period="day",
                count=20,
                currentness="live",
            )
        )


def test_tencent_bar_channel_is_period_deterministic() -> None:
    planner = QueryPlanner()
    minute = planner.compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider="tencent",
            period="1min",
            count=20,
        )
    )
    daily = planner.compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider="tencent",
            period="day",
            count=20,
        )
    )
    assert minute.channel == "minute_kline"
    assert daily.channel == "kline"
    assert minute.fingerprint != daily.fingerprint


def test_query_spec_is_immutable() -> None:
    spec = QuerySpec.build("quotes", symbols="sh600519")
    with pytest.raises(FrozenInstanceError):
        spec.provider = "tencent"  # type: ignore[misc]


def test_cache_hit_preserves_direct_origin() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols="sh600519"))
    direct = Provenance.direct(plan, observed_at_ns=1)
    cached = direct.cached("l1")
    assert direct.direct_fetch is True
    assert cached.direct_fetch is False
    assert cached.cache_hit is True
    assert cached.real is True
    assert cached.kind is ProvenanceKind.DIRECT


def test_query_result_rejects_provenance_identity_mismatch() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols="sh600519"))
    provenance = Provenance.direct(plan, observed_at_ns=1)
    result = QueryResult.from_plan(
        [{"code": "600519"}], plan=plan, provenance=provenance
    )
    assert result.meta.provider == "tdx"
    assert result.meta.provenance.kind is ProvenanceKind.DIRECT

    bad = Provenance(
        provider="tencent",
        channel="quote",
        capability="quotes",
        kind=ProvenanceKind.DIRECT,
        observed_at_ns=1,
        requested_provider="tencent",
    )
    with pytest.raises(ValidationError, match="identity"):
        QueryResult.from_plan([], plan=plan, provenance=bad)
