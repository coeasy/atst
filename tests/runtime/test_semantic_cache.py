from __future__ import annotations

from dataclasses import replace

import pytest

from tstdx.cache_semantic import SemanticCacheEntry, SemanticResultCache
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, ProvenanceKind, QueryResult


def _plan(*, provider: str = "tdx", period: str = "day"):
    return QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols=["sh600519"],
            provider=provider,
            period=period,
            count=10,
        )
    )


def _result(plan, kind: ProvenanceKind = ProvenanceKind.DIRECT):
    provenance = Provenance(
        provider=plan.provider,
        channel=plan.channel,
        capability=plan.spec.capability,
        kind=kind,
        observed_at_ns=123,
        requested_provider=plan.provider,
    )
    return QueryResult.from_plan([{"close": 1.0}], plan=plan, provenance=provenance)


@pytest.mark.parametrize(
    "kind",
    [ProvenanceKind.DIRECT, ProvenanceKind.REPLAY, ProvenanceKind.SYNTHETIC],
)
def test_cache_hit_preserves_origin_kind(kind: ProvenanceKind) -> None:
    plan = _plan()
    cache = SemanticResultCache(tier="l1")
    cache.put(plan, _result(plan, kind), ttl=10, now_ns=1_000_000_000)

    hit = cache.get(plan, now_ns=2_000_000_000)
    assert hit is not None
    assert hit.meta.provenance.kind is kind
    assert hit.meta.provenance.cache_tier == "l1"
    assert hit.meta.provenance.real is (kind is ProvenanceKind.DIRECT)


def test_cached_input_does_not_persist_previous_cache_tier() -> None:
    plan = _plan()
    original = _result(plan)
    cached_once = QueryResult.from_plan(
        original.data,
        plan=plan,
        provenance=original.meta.provenance.cached("old-tier"),
    )
    cache = SemanticResultCache(tier="new-tier")
    cache.put(plan, cached_once, ttl=None, now_ns=100)
    hit = cache.get(plan, now_ns=200)
    assert hit is not None
    assert hit.meta.provenance.cache_tier == "new-tier"
    assert hit.meta.provenance.kind is ProvenanceKind.DIRECT


def test_expired_entry_is_a_miss_and_removed() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=1, now_ns=1_000_000_000)
    assert cache.get(plan, now_ns=2_000_000_000) is None
    assert plan.fingerprint.value not in cache._data
    assert cache.misses == 1


def test_exact_expiry_boundary_is_expired() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=1, now_ns=1_000_000_000)
    assert cache.get(plan, now_ns=2_000_000_000) is None


def test_different_provider_never_shares_semantic_cache() -> None:
    tdx = _plan(provider="tdx")
    eastmoney = _plan(provider="eastmoney")
    assert tdx.fingerprint.value != eastmoney.fingerprint.value
    cache = SemanticResultCache()
    cache.put(tdx, _result(tdx), ttl=None, now_ns=100)
    assert cache.get(eastmoney, now_ns=200) is None


def test_tampered_channel_identity_fails_closed() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=None, now_ns=100)
    key = plan.fingerprint.value
    entry = cache._data[key]
    cache._data[key] = replace(entry, channel="wrong")
    assert cache.get(plan, now_ns=200) is None


def test_tampered_fingerprint_fails_closed() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=None, now_ns=100)
    key = plan.fingerprint.value
    cache._data[key] = replace(cache._data[key], fingerprint="q1:wrong")
    assert cache.get(plan, now_ns=200) is None


def test_tampered_provenance_identity_fails_closed() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=None, now_ns=100)
    key = plan.fingerprint.value
    entry = cache._data[key]
    bad_provenance = replace(entry.provenance, provider="eastmoney")
    cache._data[key] = replace(entry, provenance=bad_provenance)
    assert cache.get(plan, now_ns=200) is None


def test_schema_version_mismatch_is_a_miss() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=None, now_ns=100)
    key = plan.fingerprint.value
    cache._data[key] = replace(cache._data[key], schema_version=999)
    assert cache.get(plan, now_ns=200) is None


def test_corrupt_entry_is_a_miss() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache._data[plan.fingerprint.value] = object()  # type: ignore[assignment]
    assert cache.get(plan, now_ns=200) is None


def test_put_rejects_result_from_another_plan() -> None:
    plan = _plan()
    other = _plan(provider="eastmoney")
    cache = SemanticResultCache()
    with pytest.raises(ValueError, match="identity"):
        cache.put(plan, _result(other), ttl=None, now_ns=100)


def test_returned_data_is_isolated_from_cached_copy() -> None:
    plan = _plan()
    cache = SemanticResultCache()
    cache.put(plan, _result(plan), ttl=None, now_ns=100)
    first = cache.get(plan, now_ns=200)
    assert first is not None
    first.data[0]["close"] = 999
    second = cache.get(plan, now_ns=300)
    assert second is not None
    assert second.data[0]["close"] == 1.0


def test_negative_ttl_rejected() -> None:
    plan = _plan()
    entry_result = _result(plan)
    with pytest.raises(ValueError):
        SemanticCacheEntry.from_result(entry_result, ttl=-1, now_ns=100)
