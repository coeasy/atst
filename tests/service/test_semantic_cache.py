from __future__ import annotations

import time

from tstdx.domain.models import Quote
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.semantic_cache import SemanticQueryCache
from tstdx.service import QueryResult


class QuoteAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbols: list[str]) -> list[Quote]:
        self.calls += 1
        return [Quote(code=symbol, price=float(self.calls)) for symbol in symbols]


class Manager:
    def __init__(self, adapter: QuoteAdapter) -> None:
        self.adapter = adapter

    def quote_adapter(self, provider: str) -> QuoteAdapter:
        assert provider == "tencent"
        return self.adapter

    def close(self) -> None:
        pass


def test_default_queries_never_read_or_write_query_cache() -> None:
    adapter = QuoteAdapter()
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)

    first = service.quotes(["sh600519"], provider="tencent")
    second = service.quotes(["sh600519"], provider="tencent")

    assert adapter.calls == 2
    assert first[0].price == 1.0
    assert second[0].price == 2.0
    assert len(cache) == 0
    assert cache.writes == 0
    service.close()


def test_positive_max_age_enables_exact_fingerprint_cache() -> None:
    adapter = QuoteAdapter()
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)

    first = service.quotes(
        ["sh600519"], provider="tencent", max_age=1.0, with_meta=True
    )
    second = service.quotes(
        ["sh600519"], provider="tencent", max_age=1.0, with_meta=True
    )

    assert isinstance(first, QueryResult)
    assert isinstance(second, QueryResult)
    assert adapter.calls == 1
    assert first.meta.freshness.origin == "direct"
    assert first.meta.freshness.cache_hit is False
    assert second.meta.freshness.origin == "cache"
    assert second.meta.freshness.cache_hit is True
    assert second.meta.fallback is False
    assert second.meta.freshness_status is not None
    assert second.meta.freshness_status.verified is True
    assert second.meta.freshness_status.basis.startswith("bounded_cache:")
    service.close()


def test_different_max_age_policies_share_one_data_identity() -> None:
    adapter = QuoteAdapter()
    service = UnifiedMarketDataService(manager=Manager(adapter))

    first = service.quotes(
        ["sh600519"], provider="tencent", max_age=2.0, with_meta=True
    )
    second = service.quotes(
        ["sh600519"], provider="tencent", max_age=1.0, with_meta=True
    )

    assert isinstance(first, QueryResult)
    assert isinstance(second, QueryResult)
    assert adapter.calls == 1
    assert len(service.query_cache) == 1
    assert second.meta.freshness.cache_hit is True
    service.close()


def test_stricter_max_age_still_refetches_shared_identity_when_too_old() -> None:
    adapter = QuoteAdapter()
    service = UnifiedMarketDataService(manager=Manager(adapter))

    service.quotes(["sh600519"], provider="tencent", max_age=1.0)
    time.sleep(0.02)
    rows = service.quotes(["sh600519"], provider="tencent", max_age=0.001)

    assert adapter.calls == 2
    assert rows[0].price == 2.0
    assert len(service.query_cache) == 1
    service.close()


def test_expired_entry_is_refetched() -> None:
    adapter = QuoteAdapter()
    service = UnifiedMarketDataService(manager=Manager(adapter))

    service.quotes(["sh600519"], provider="tencent", max_age=0.01)
    time.sleep(0.02)
    rows = service.quotes(["sh600519"], provider="tencent", max_age=0.01)

    assert adapter.calls == 2
    assert rows[0].price == 2.0
    service.close()


def test_injected_empty_cache_instance_is_preserved() -> None:
    adapter = QuoteAdapter()
    cache = SemanticQueryCache(max_entries=7)
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)

    assert service.query_cache is cache
    service.close()
