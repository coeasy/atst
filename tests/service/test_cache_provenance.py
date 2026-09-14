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


def _result(
    *,
    provider: str,
    channel: str = "quote",
    capability: str = "quotes",
    origin: str = "direct",
    replay: bool = False,
    synthetic: bool = False,
    real: bool = True,
    fallback: bool = False,
) -> QueryResult[list[Quote]]:
    observed = time.time_ns()
    return QueryResult(
        data=[Quote(code="sh600519", price=999.0)],
        meta=ResultMeta(
            provider=provider,
            channel=channel,
            capability=capability,
            observed_at_ns=observed,
            freshness=FreshnessEvidence(
                origin=origin,
                observed_at_ns=observed,
                replay=replay,
                synthetic=synthetic,
            ),
            real=real,
            fallback=fallback,
        ),
    )


def _plan(service: UnifiedMarketDataService):  # noqa: ANN202
    return service.compile(
        QuerySpec.build(
            "quotes",
            symbols=("sh600519",),
            provider="tencent",
            max_age=5.0,
        )
    )


def test_cross_provider_cache_entry_is_invalidated_and_refetched() -> None:
    adapter = QuoteAdapter()
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)
    plan = _plan(service)
    cache.put(plan.fingerprint.value, _result(provider="sina"))

    result = service.query(plan.spec, with_meta=True)

    assert isinstance(result, QueryResult)
    assert adapter.calls == 1
    assert result.data[0].price == 1.0
    assert result.meta.provider == "tencent"
    assert result.meta.channel == plan.channel
    assert result.meta.capability == plan.spec.capability
    assert result.meta.freshness.origin == "direct"

    cached = cache.get(plan.fingerprint.value, max_age=5.0)
    assert isinstance(cached, QueryResult)
    assert cached.meta.provider == "tencent"
    service.close()


@pytest.mark.parametrize(
    "poisoned",
    [
        _result(provider="tencent", origin="replay", replay=True, real=False),
        _result(provider="tencent", origin="synthetic", synthetic=True, real=False),
        _result(provider="tencent", fallback=True),
    ],
)
def test_non_direct_cache_provenance_never_short_circuits_provider(poisoned) -> None:  # noqa: ANN001
    adapter = QuoteAdapter()
    cache = SemanticQueryCache()
    service = UnifiedMarketDataService(manager=Manager(adapter), query_cache=cache)
    plan = _plan(service)
    cache.put(plan.fingerprint.value, poisoned)

    result = service.query(plan.spec, with_meta=True)

    assert isinstance(result, QueryResult)
    assert adapter.calls == 1
    assert result.data[0].price == 1.0
    assert result.meta.provider == "tencent"
    assert result.meta.fallback is False
    assert result.meta.freshness.origin == "direct"
    service.close()
