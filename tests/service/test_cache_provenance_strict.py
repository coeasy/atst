from __future__ import annotations

import time

import pytest

from tstdx.domain.models import Quote
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.query import QuerySpec
from tstdx.semantic_cache import SemanticQueryCache
from tstdx.service import FreshnessEvidence, QueryResult, ResultMeta


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


def _poisoned(
    *,
    channel: str = "quote",
    capability: str = "quotes",
    meta_real: bool = True,
    origin: str = "direct",
    cache_hit: bool = False,
) -> QueryResult[list[Quote]]:
    observed = time.time_ns()
    return QueryResult(
        data=[Quote(code="sh600519", price=999.0)],
        meta=ResultMeta(
            provider="tencent",
            channel=channel,
            capability=capability,
            observed_at_ns=observed,
            freshness=FreshnessEvidence(
                origin=origin,
                observed_at_ns=observed,
                cache_hit=cache_hit,
            ),
            real=meta_real,
            fallback=False,
        ),
    )


@pytest.mark.parametrize(
    "poisoned",
    [
        _poisoned(channel="minute"),
        _poisoned(capability="bars"),
        _poisoned(meta_real=False),
        _poisoned(origin="cache", cache_hit=True),
    ],
)
def test_mismatched_or_recursive_cache_provenance_is_invalidated(poisoned) -> None:  # noqa: ANN001
    adapter = QuoteAdapter()
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)
    plan = service.compile(
        QuerySpec.build(
            "quotes",
            symbols=("sh600519",),
            provider="tencent",
            max_age=5.0,
        )
    )
    cache.put(plan.fingerprint.value, poisoned)

    result = service.query(plan.spec, with_meta=True)

    assert isinstance(result, QueryResult)
    assert adapter.calls == 1
    assert result.data[0].price == 1.0
    assert result.meta.provider == plan.provider
    assert result.meta.channel == plan.channel
    assert result.meta.capability == plan.spec.capability
    assert result.meta.freshness.origin == "direct"
    assert result.meta.freshness.cache_hit is False

    refreshed = cache.get(plan.fingerprint.value, max_age=5.0)
    assert isinstance(refreshed, QueryResult)
    assert refreshed.meta.provider == plan.provider
    assert refreshed.meta.channel == plan.channel
    assert refreshed.meta.capability == plan.spec.capability
    service.close()
