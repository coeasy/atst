from __future__ import annotations

import time
from pathlib import Path

from tstdx.cache_v2 import SQLiteSemanticQueryCache, TieredSemanticQueryCache
from tstdx.domain.models import Bar, Quote
from tstdx.freshness import FreshnessMode, FreshnessStatus
from tstdx.semantic_cache import SemanticQueryCache
from tstdx.service import FreshnessEvidence, QueryResult, ResultMeta


def _meta(capability: str, channel: str) -> ResultMeta:
    observed = time.time_ns()
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
            mode=FreshnessMode.DIRECT_SNAPSHOT,
            basis="test",
            provider_timestamp=None,
            observed_age_seconds=0.0,
            currentness_verified=True,
        ),
        real=True,
        fallback=False,
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
            data=[Bar(code="sh600519", datetime="2026-09-08", close=123.4)],
            meta=_meta("bars", "kline"),
        )
        cache.put(key, original)
        restored = cache.get(key, max_age=10.0)
        assert restored.data[0].code == "sh600519"
        assert restored.data[0].datetime == "2026-09-08"
        assert restored.data[0].close == 123.4
    finally:
        cache.close()


def test_expired_l2_entry_becomes_miss_and_is_deleted(tmp_path: Path) -> None:
    cache = SQLiteSemanticQueryCache(tmp_path / "expire.sqlite3")
    try:
        key = "q1:expire"
        cache.put(
            key,
            QueryResult(
                data=[Quote(code="sh600519", price=1.0)],
                meta=_meta("quotes", "quote"),
            ),
        )
        time.sleep(0.02)
        assert cache.get(key, max_age=0.001) is None
        assert len(cache) == 0
    finally:
        cache.close()


def test_tiered_invalidate_removes_both_l1_and_l2(tmp_path: Path) -> None:
    l1 = SemanticQueryCache()
    l2 = SQLiteSemanticQueryCache(tmp_path / "tiered.sqlite3")
    cache = TieredSemanticQueryCache(l1, l2)
    key = "q1:tiered"
    value = QueryResult(
        data=[Quote(code="sh600519", price=1.0)],
        meta=_meta("quotes", "quote"),
    )
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
    value = QueryResult(
        data=[Quote(code="sh600519", price=2.0)],
        meta=_meta("quotes", "quote"),
    )
    try:
        l2.put(key, value)
        assert len(l1) == 0
        restored = cache.get(key, max_age=10.0)
        assert restored.data[0].price == 2.0
        assert len(l1) == 1
    finally:
        cache.close()
