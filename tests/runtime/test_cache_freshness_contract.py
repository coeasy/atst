from __future__ import annotations

from tstdx.cache_semantic import SemanticResultCache
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, ProvenanceKind, QueryResult


def _plan(*, max_age: float | None, currentness: str = "auto"):
    return QueryPlanner().compile(
        QuerySpec.build(
            "quotes",
            symbols=["sh600519"],
            provider="tdx",
            currentness=currentness,
            max_age=max_age,
        )
    )


def _result(plan, *, kind: ProvenanceKind, observed_at_ns: int):
    provenance = Provenance(
        provider=plan.provider,
        channel=plan.channel,
        capability=plan.spec.capability,
        kind=kind,
        observed_at_ns=observed_at_ns,
        requested_provider=plan.provider,
    )
    return QueryResult.from_plan(
        [{"code": "600519", "price": 1.0}],
        plan=plan,
        provenance=provenance,
    )


def test_max_age_is_a_cache_policy_not_part_of_the_data_fingerprint() -> None:
    """v13 SSOT：``max_age`` 是新鲜度策略，不是数据身份的一部分。

    同一份数据的宽松 / 严格上界共享同一个 QueryFingerprint（数据身份一致），
    但不得共享 L1 缓存槽：宽松条目不能回答更严格的查询。
    """

    loose = _plan(max_age=5.0)
    strict = _plan(max_age=1.0)

    assert loose.fingerprint.value == strict.fingerprint.value
    assert '"max_age"' not in loose.fingerprint.canonical
    assert '"max_age"' not in strict.fingerprint.canonical

    cache = SemanticResultCache()
    cache.put(
        loose,
        _result(loose, kind=ProvenanceKind.DIRECT, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )
    # 已在宽松策略下缓存的条目，不得被更严格的 max_age 复用。
    assert cache.get(strict, now_ns=1_000_000_001) is None
    # 相同策略仍然命中。
    assert cache.get(loose, now_ns=1_000_000_001) is not None


def test_cache_hit_respects_query_max_age_boundary() -> None:
    plan = _plan(max_age=1.0)
    cache = SemanticResultCache()
    cache.put(
        plan,
        _result(plan, kind=ProvenanceKind.DIRECT, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )

    assert cache.get(plan, now_ns=2_000_000_000) is not None


def test_cache_rejects_result_older_than_query_max_age_even_before_ttl() -> None:
    plan = _plan(max_age=1.0)
    cache = SemanticResultCache()
    cache.put(
        plan,
        _result(plan, kind=ProvenanceKind.DIRECT, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )

    assert cache.get(plan, now_ns=2_000_000_001) is None
    assert plan.fingerprint.value not in cache._data


def test_live_query_rejects_replay_cache_origin() -> None:
    plan = _plan(max_age=5.0, currentness="live")
    cache = SemanticResultCache()
    cache.put(
        plan,
        _result(plan, kind=ProvenanceKind.REPLAY, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )

    assert cache.get(plan, now_ns=1_500_000_000) is None


def test_live_query_rejects_synthetic_cache_origin() -> None:
    plan = _plan(max_age=5.0, currentness="live")
    cache = SemanticResultCache()
    cache.put(
        plan,
        _result(plan, kind=ProvenanceKind.SYNTHETIC, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )

    assert cache.get(plan, now_ns=1_500_000_000) is None


def test_live_query_accepts_fresh_direct_cache_origin() -> None:
    plan = _plan(max_age=5.0, currentness="live")
    cache = SemanticResultCache(tier="l1")
    cache.put(
        plan,
        _result(plan, kind=ProvenanceKind.DIRECT, observed_at_ns=1_000_000_000),
        ttl=30.0,
        now_ns=1_000_000_000,
    )

    hit = cache.get(plan, now_ns=1_500_000_000)
    assert hit is not None
    assert hit.meta.provenance.kind is ProvenanceKind.DIRECT
    assert hit.meta.provenance.cache_tier == "l1"
