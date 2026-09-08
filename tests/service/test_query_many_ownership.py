from __future__ import annotations

from typing import Any

from tstdx.domain.models import Level, Quote
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.query import QuerySpec
from tstdx.service import QueryResult


class QuoteAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbols: list[str]) -> list[Quote]:
        self.calls += 1
        return [
            Quote(
                code=symbol,
                price=float(self.calls),
                bid=[Level(price=9.9, volume=100)],
                extra={"nested": {"rank": 1}},
            )
            for symbol in symbols
        ]


class Manager:
    def __init__(self, adapter: QuoteAdapter) -> None:
        self.adapter = adapter

    def quote_adapter(self, provider: str) -> QuoteAdapter:
        assert provider == "tencent"
        return self.adapter

    def close(self) -> None:
        pass


class RecordingMissCache:
    def __init__(self) -> None:
        self.max_ages: list[float] = []

    def get(self, key: str, *, max_age: float) -> None:  # noqa: ARG002
        self.max_ages.append(max_age)
        return None

    def put(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def invalidate(self, key: str) -> bool:  # noqa: ARG002
        return False


def test_query_many_duplicate_fanout_deep_copies_mutable_models() -> None:
    adapter = QuoteAdapter()
    service = UnifiedMarketDataService(
        manager=Manager(adapter),
        default_provider="tencent",
    )
    spec = QuerySpec.build("quotes", symbols=["sh600519"], provider="tencent")
    try:
        results = service.query_many([spec, spec], with_meta=True)

        assert adapter.calls == 1
        first, second = results
        assert isinstance(first, QueryResult)
        assert isinstance(second, QueryResult)
        assert first is not second
        assert first.data is not second.data
        assert first.data[0] is not second.data[0]
        assert first.data[0].bid is not second.data[0].bid
        assert first.data[0].bid[0] is not second.data[0].bid[0]
        assert first.data[0].extra is not second.data[0].extra
        assert first.data[0].extra["nested"] is not second.data[0].extra["nested"]

        first.data[0].bid[0].volume = 999
        first.data[0].extra["nested"]["rank"] = 99
        assert second.data[0].bid[0].volume == 100
        assert second.data[0].extra["nested"]["rank"] == 1
    finally:
        service.close()


def test_query_many_same_data_fingerprint_keeps_distinct_max_age_policy() -> None:
    adapter = QuoteAdapter()
    cache = RecordingMissCache()
    service = UnifiedMarketDataService(
        manager=Manager(adapter),
        query_cache=cache,
        default_provider="tencent",
    )
    short = QuerySpec.build(
        "quotes",
        symbols=["sh600519"],
        provider="tencent",
        max_age=1.0,
    )
    long = QuerySpec.build(
        "quotes",
        symbols=["sh600519"],
        provider="tencent",
        max_age=30.0,
    )
    try:
        short_plan = service.compile(short)
        long_plan = service.compile(long)
        assert short_plan.fingerprint.value == long_plan.fingerprint.value

        results = service.query_many([short, long], with_meta=True)

        assert len(results) == 2
        assert adapter.calls == 2
        assert cache.max_ages == [1.0, 30.0]
    finally:
        service.close()
