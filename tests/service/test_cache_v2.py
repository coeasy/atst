from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tstdx.cache_v2 import SQLiteSemanticQueryCache, TieredSemanticQueryCache
from tstdx.domain.models import Bar, Quote
from tstdx.freshness import FreshnessMode, FreshnessStatus
from tstdx.semantic_cache import SemanticQueryCache
from tstdx.service import FreshnessEvidence, QueryResult, ResultMeta


def _meta(capability: str, channel: str) -> ResultMeta:
    observed = time.time_ns()
    mode = (
        FreshnessMode.DIRECT_SNAPSHOT
        if capability == "quotes"
        else FreshnessMode.CURRENT_SERIES
    )
    return ResultMeta(
        provider="tencent",
        channel=channel,
        capability=capability,
        observed_at_ns=observed,
        freshness=FreshnessEvidence(
            origin="direct",
            observed_at_ns=observed,
            provider_timestamp=None,
            cache_hit=False,
            replay=False,
            synthetic=False,
        ),
        freshness_status=FreshnessStatus(
            verified=True,
            mode=mode,
            basis="test",
            provider_timestamp=None,
            observed_age_seconds=0.0,
            currentness_verified=True,
        ),
        real=True,
        fallback=False,
    )


def _poisoned_quote() -> QueryResult[list[Quote]]:
    observed = time.time_ns()
    return QueryResult(
        data=[Quote(code="sh600519", price=99.0)],
        meta=ResultMeta(
            provider="tencent",
            channel="quote",
            capability="quotes",
            observed_at_ns=observed,
            freshness=FreshnessEvidence(
                origin="cache",
                observed_at_ns=observed,
                cache_hit=True,
            ),
            freshness_status=FreshnessStatus(
                verified=True,
                mode=FreshnessMode.DIRECT_SNAPSHOT,
                basis="poisoned",
                provider_timestamp=None,
                observed_age_seconds=0.0,
                currentness_verified=True,
            ),
            real=True,
            fallback=False,
        ),
    )


def _quote_result(price: float) -> QueryResult[list[Quote]]:
    return QueryResult(
        data=[Quote(code="sh600519", price=price)],
        meta=_meta("quotes", "quote"),
    )


def test_sqlite_quote_roundtrip_without_pickle(tmp_path: Path) -> None:
    path = tmp_path / "semantic.sqlite3"
    cache = SQLiteSemanticQueryCache(path)
    key = "q1:quotes"
    original = QueryResult(
        data=[Quote(code="sh600519", price=10.5, extra={"name": "test"})],
        meta=_meta("quotes", "quote"),
    )
    cache.put(key, original)
    cache.close()

    reopened = SQLiteSemanticQueryCache(path)
    try:
        restored = reopened.get(key, max_age=10.0)
        assert isinstance(restored, QueryResult)
        assert restored.data[0].code == "sh600519"
        assert restored.data[0].price == 10.5
        assert restored.data[0].extra["name"] == "test"
        assert restored.meta.provider == "tencent"
        assert restored.meta.freshness.origin == "direct"
    finally:
        reopened.close()


def test_sqlite_bar_roundtrip(tmp_path: Path) -> None:
    cache = SQLiteSemanticQueryCache(tmp_path / "bars.sqlite3")
    try:
        key = "q1:bars"
        original = QueryResult(
            data=[Bar(datetime="2026-09-08", close=123.4, extra={"source_row": 7})],
            meta=_meta("bars", "kline"),
        )
        cache.put(key, original)
        restored = cache.get(key, max_age=10.0)
        assert isinstance(restored, QueryResult)
        assert restored.data[0].datetime == "2026-09-08"
        assert restored.data[0].close == 123.4
        assert restored.data[0].extra["source_row"] == 7
        status = restored.meta.freshness_status
        assert status is not None
        assert status.mode is FreshnessMode.CURRENT_SERIES
    finally:
        cache.close()


def test_sqlite_rejects_poisoned_provenance_before_write(tmp_path: Path) -> None:
    cache = SQLiteSemanticQueryCache(tmp_path / "poison-write.sqlite3")
    try:
        before_errors = cache.errors
        cache.put("q1:poison", _poisoned_quote())
        assert len(cache) == 0
        assert cache.writes == 0
        assert cache.errors == before_errors + 1
    finally:
        cache.close()


def test_tiered_rejects_poison_before_touching_l1_or_l2(tmp_path: Path) -> None:
    l1 = SemanticQueryCache()
    l2 = SQLiteSemanticQueryCache(tmp_path / "poison-tiered.sqlite3")
    cache = TieredSemanticQueryCache(l1, l2)
    try:
        with pytest.raises(ValueError, match="direct upstream provenance"):
            cache.put("q1:poison", _poisoned_quote())
        assert len(l1) == 0
        assert len(l2) == 0
    finally:
        cache.close()


def test_expired_l2_entry_becomes_miss_and_is_deleted(tmp_path: Path) -> None:
    cache = SQLiteSemanticQueryCache(tmp_path / "expire.sqlite3")
    try:
        key = "q1:expire"
        cache.put(key, _quote_result(1.0))
        time.sleep(0.02)
        assert cache.get(key, max_age=0.001) is None
        assert len(cache) == 0
    finally:
        cache.close()


def test_corrupt_l2_payload_is_evicted_after_one_failed_decode(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.sqlite3"
    cache = SQLiteSemanticQueryCache(path)
    key = "q1:corrupt"
    try:
        cache.put(key, _quote_result(1.0))
        with sqlite3.connect(path) as raw:
            raw.execute(
                "UPDATE semantic_cache SET payload=? WHERE fingerprint=?",
                ("{not-json", key),
            )
            raw.commit()

        before_errors = cache.errors
        assert cache.get(key, max_age=10.0) is None
        assert cache.errors == before_errors + 1
        assert len(cache) == 0

        before_errors = cache.errors
        assert cache.get(key, max_age=10.0) is None
        assert cache.errors == before_errors
    finally:
        cache.close()


def test_decoded_l2_provenance_poison_is_evicted_not_promoted(tmp_path: Path) -> None:
    path = tmp_path / "provenance-corrupt.sqlite3"
    cache = SQLiteSemanticQueryCache(path)
    key = "q1:provenance-corrupt"
    try:
        cache.put(key, _quote_result(1.0))
        with sqlite3.connect(path) as raw:
            row = raw.execute(
                "SELECT payload FROM semantic_cache WHERE fingerprint=?", (key,)
            ).fetchone()
            assert row is not None
            payload = json.loads(str(row[0]))
            payload["meta"]["fallback"] = True
            raw.execute(
                "UPDATE semantic_cache SET payload=? WHERE fingerprint=?",
                (json.dumps(payload, ensure_ascii=False), key),
            )
            raw.commit()

        before_errors = cache.errors
        assert cache.get(key, max_age=10.0) is None
        assert cache.errors == before_errors + 1
        assert len(cache) == 0
    finally:
        cache.close()


def test_valid_payload_copied_under_another_fingerprint_is_evicted(tmp_path: Path) -> None:
    path = tmp_path / "fingerprint-swap.sqlite3"
    cache = SQLiteSemanticQueryCache(path)
    first = "q1:first"
    second = "q1:second"
    try:
        cache.put(first, _quote_result(1.0))
        cache.put(second, _quote_result(2.0))
        with sqlite3.connect(path) as raw:
            first_payload = raw.execute(
                "SELECT payload FROM semantic_cache WHERE fingerprint=?", (first,)
            ).fetchone()
            assert first_payload is not None
            raw.execute(
                "UPDATE semantic_cache SET payload=? WHERE fingerprint=?",
                (first_payload[0], second),
            )
            raw.commit()

        before_errors = cache.errors
        assert cache.get(second, max_age=10.0) is None
        assert cache.errors == before_errors + 1
        assert len(cache) == 1

        still_valid = cache.get(first, max_age=10.0)
        assert isinstance(still_valid, QueryResult)
        assert still_valid.data[0].price == 1.0
    finally:
        cache.close()


def test_future_dated_l2_storage_timestamp_is_evicted(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    cache = SQLiteSemanticQueryCache(path)
    key = "q1:future"
    try:
        cache.put(key, _quote_result(1.0))
        with sqlite3.connect(path) as raw:
            raw.execute(
                "UPDATE semantic_cache SET stored_wall_ns=? WHERE fingerprint=?",
                (time.time_ns() + 60_000_000_000, key),
            )
            raw.commit()

        assert cache.get(key, max_age=120.0) is None
        assert len(cache) == 0
    finally:
        cache.close()


def test_tiered_invalidate_removes_both_l1_and_l2(tmp_path: Path) -> None:
    l1 = SemanticQueryCache()
    l2 = SQLiteSemanticQueryCache(tmp_path / "tiered.sqlite3")
    cache = TieredSemanticQueryCache(l1, l2)
    key = "q1:tiered"
    value = _quote_result(1.0)
    try:
        cache.put(key, value)
        assert len(l1) == 1
        assert len(l2) == 1
        assert cache.invalidate(key) is True
        assert len(l1) == 0
        assert len(l2) == 0
        assert cache.get(key, max_age=10.0) is None
    finally:
        cache.close()


def test_l2_hit_promotes_back_to_l1(tmp_path: Path) -> None:
    l1 = SemanticQueryCache()
    l2 = SQLiteSemanticQueryCache(tmp_path / "promote.sqlite3")
    cache = TieredSemanticQueryCache(l1, l2)
    key = "q1:promote"
    value = _quote_result(2.0)
    try:
        l2.put(key, value)
        assert len(l1) == 0
        restored = cache.get(key, max_age=10.0)
        assert isinstance(restored, QueryResult)
        assert restored.data[0].price == 2.0
        assert len(l1) == 1
    finally:
        cache.close()


def test_l2_initialization_failure_is_disabled_cache_not_service_failure(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")

    cache = SQLiteSemanticQueryCache(blocked_parent / "semantic.sqlite3")
    assert cache.enabled is False
    assert cache.errors >= 1
    assert cache.get("q1:disabled", max_age=10.0) is None
    cache.put("q1:disabled", _quote_result(3.0))
    assert cache.invalidate("q1:disabled") is False
    cache.clear()
    cache.close()
    cache.close()
