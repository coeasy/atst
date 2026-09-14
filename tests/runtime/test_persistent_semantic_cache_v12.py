from __future__ import annotations

import json
import time

from tstdx.cache_persistent import PersistentSemanticCache
from tstdx.domain.models import Bar, Level, Quote
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, ProvenanceKind, QueryResult, ResultMeta
from tstdx.runtime import UnifiedRuntime


def _quote_plan(*, max_age: float | None = 5.0):
    return QueryPlanner().compile(
        QuerySpec.build(
            "quotes",
            symbols=["sh600519"],
            provider="tdx",
            currentness="live",
            max_age=max_age,
        )
    )


def _direct_result(plan, *, observed_at_ns: int):
    quote = Quote(
        code="600519",
        price=100.0,
        bid=[Level(price=99.9, volume=100)],
        ask=[Level(price=100.1, volume=200)],
        extra={"marker": "direct"},
    )
    return QueryResult.from_plan(
        [quote],
        plan=plan,
        provenance=Provenance.direct(plan, observed_at_ns=observed_at_ns),
    )


def test_l2_roundtrip_preserves_domain_type_and_direct_origin(tmp_path) -> None:
    plan = _quote_plan()
    now = 10_000_000_000
    result = _direct_result(plan, observed_at_ns=now)
    with PersistentSemanticCache(tmp_path / "semantic.sqlite") as cache:
        assert cache.put(plan, result, ttl=10.0, now_ns=now) is True
        hit = cache.get(plan, now_ns=now + 1)
    assert hit is not None
    assert isinstance(hit.data[0], Quote)
    assert isinstance(hit.data[0].bid[0], Level)
    assert hit.meta.provenance.kind is ProvenanceKind.DIRECT
    assert hit.meta.provenance.cache_tier == "l2"


def test_l2_rejects_replay_and_synthetic_persistence(tmp_path) -> None:
    plan = _quote_plan()
    now = 10_000_000_000
    with PersistentSemanticCache(tmp_path / "semantic.sqlite") as cache:
        for kind in (ProvenanceKind.REPLAY, ProvenanceKind.SYNTHETIC):
            provenance = Provenance(
                provider=plan.provider,
                channel=plan.channel,
                capability=plan.spec.capability,
                kind=kind,
                observed_at_ns=now,
                requested_provider=plan.provider,
            )
            result = QueryResult(
                data=[{"code": "600519"}],
                meta=ResultMeta.from_plan(plan, provenance),
            )
            assert cache.put(plan, result, ttl=10.0, now_ns=now) is False
        assert cache.get(plan, now_ns=now + 1) is None


def test_l2_hash_blocks_row_copy_poisoning(tmp_path) -> None:
    first = _quote_plan(max_age=5.0)
    second = _quote_plan(max_age=1.0)
    now = 10_000_000_000
    with PersistentSemanticCache(tmp_path / "semantic.sqlite") as cache:
        assert cache.put(first, _direct_result(first, observed_at_ns=now), ttl=10.0, now_ns=now)
        row = cache._db.execute(
            """
            SELECT schema_version, codec_version, provider, channel, capability,
                   stored_at_ns, expires_at_ns, provenance_json, data_json, payload_hash
            FROM semantic_cache_v2 WHERE fingerprint = ?
            """,
            (first.fingerprint.value,),
        ).fetchone()
        assert row is not None
        cache._db.execute(
            """
            INSERT OR REPLACE INTO semantic_cache_v2
            (fingerprint, schema_version, codec_version, provider, channel, capability,
             stored_at_ns, expires_at_ns, provenance_json, data_json, payload_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (second.fingerprint.value, *row),
        )
        cache._db.commit()
        assert cache.get(second, now_ns=now + 1) is None


def test_l2_tampered_provenance_fails_hash_before_decode(tmp_path) -> None:
    plan = _quote_plan()
    now = 10_000_000_000
    with PersistentSemanticCache(tmp_path / "semantic.sqlite") as cache:
        assert cache.put(plan, _direct_result(plan, observed_at_ns=now), ttl=10.0, now_ns=now)
        row = cache._db.execute(
            "SELECT provenance_json FROM semantic_cache_v2 WHERE fingerprint = ?",
            (plan.fingerprint.value,),
        ).fetchone()
        assert row is not None
        provenance = json.loads(row[0])
        provenance["provider"] = "eastmoney"
        cache._db.execute(
            "UPDATE semantic_cache_v2 SET provenance_json = ? WHERE fingerprint = ?",
            (json.dumps(provenance), plan.fingerprint.value),
        )
        cache._db.commit()
        assert cache.get(plan, now_ns=now + 1) is None


def test_runtime_promotes_verified_l2_without_provider_io(tmp_path, monkeypatch) -> None:
    plan = _quote_plan(max_age=30.0)
    now = time.time_ns()
    l2 = PersistentSemanticCache(tmp_path / "semantic.sqlite")
    assert l2.put(plan, _direct_result(plan, observed_at_ns=now), ttl=30.0, now_ns=now)
    runtime = UnifiedRuntime(persistent_cache=l2, cache_ttl=5.0)

    def no_io(_plan):
        raise AssertionError("Direct Provider must not run on verified L2 hit")

    monkeypatch.setattr(runtime.executor, "execute", no_io)
    hit = runtime.quotes("sh600519", provider="tdx", currentness="live", max_age=30.0)
    assert hit.meta.provenance.cache_tier == "l2"

    l1 = runtime.quotes("sh600519", provider="tdx", currentness="live", max_age=30.0)
    assert l1.meta.provenance.cache_tier == "l1"
    l2.close()


def test_l2_roundtrip_bar_type(tmp_path) -> None:
    plan = QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider="tdx",
            period="day",
            count=1,
            currentness="historical",
        )
    )
    now = 10_000_000_000
    result = QueryResult.from_plan(
        [Bar(datetime="2026-09-12", open=1, high=2, low=1, close=2, volume=10)],
        plan=plan,
        provenance=Provenance.direct(plan, observed_at_ns=now),
    )
    with PersistentSemanticCache(tmp_path / "semantic.sqlite") as cache:
        assert cache.put(plan, result, ttl=10.0, now_ns=now)
        hit = cache.get(plan, now_ns=now + 1)
    assert hit is not None
    assert isinstance(hit.data[0], Bar)
