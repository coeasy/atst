from __future__ import annotations

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import SourceUnavailable, ValidationError
from tstdx.health import SourceHealthRegistry
from tstdx.planned_service import UnifiedMarketDataService


class FailingQuoteAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = True

    def fetch(self, symbols: list[str]) -> list[Quote]:
        self.calls += 1
        if self.fail:
            raise SourceUnavailable("upstream down")
        return [Quote(code=symbol, price=10.0) for symbol in symbols]


class Manager:
    def __init__(self, adapter: FailingQuoteAdapter) -> None:
        self.adapter = adapter

    def quote_adapter(self, provider: str) -> FailingQuoteAdapter:
        assert provider == "tencent"
        return self.adapter

    def close(self) -> None:
        pass


def test_health_is_scoped_by_provider_channel_capability() -> None:
    health = SourceHealthRegistry(failure_threshold=2, cooldown_seconds=60)
    health.record_failure(
        "tencent", "quote", "quotes", SourceUnavailable("down")
    )
    quotes = health.snapshot("tencent", "quote", "quotes")
    assert quotes.consecutive_failures == 1

    bars = health.snapshot("tencent", "kline", "bars")
    assert bars.consecutive_failures == 0
    assert bars.circuit_open is False


def test_validation_errors_do_not_penalize_provider_health() -> None:
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    exc = ValidationError("bad input")
    health.record_failure(
        "tencent",
        "quote",
        "quotes",
        exc,
        penalize=health.should_penalize(exc),
    )
    state = health.snapshot("tencent", "quote", "quotes")
    assert state.failures == 1
    assert state.consecutive_failures == 0
    assert state.circuit_open is False


def test_planned_service_health_gate_stops_repeated_upstream_calls() -> None:
    adapter = FailingQuoteAdapter()
    health = SourceHealthRegistry(failure_threshold=2, cooldown_seconds=60)
    service = UnifiedMarketDataService(
        manager=Manager(adapter),
        health=health,
        default_provider="tencent",
    )
    try:
        for _ in range(2):
            with pytest.raises(SourceUnavailable):
                service.quotes(["sh600519"])
        assert adapter.calls == 2
        state = health.snapshot("tencent", "quote", "quotes")
        assert state.circuit_open is True

        with pytest.raises(SourceUnavailable) as caught:
            service.quotes(["sh600519"])
        assert adapter.calls == 2
        assert caught.value.context["circuit_open"] is True
        assert caught.value.context["phase"] == "health_gate"
        assert caught.value.context["provider_switch_allowed"] is False
    finally:
        service.close()


def test_success_resets_consecutive_failures_after_manual_recovery() -> None:
    adapter = FailingQuoteAdapter()
    health = SourceHealthRegistry(failure_threshold=3, cooldown_seconds=60)
    service = UnifiedMarketDataService(
        manager=Manager(adapter), health=health, default_provider="tencent"
    )
    try:
        with pytest.raises(SourceUnavailable):
            service.quotes(["sh600519"])
        assert health.snapshot("tencent", "quote", "quotes").consecutive_failures == 1

        adapter.fail = False
        rows = service.quotes(["sh600519"])
        assert rows[0].code == "sh600519"
        state = health.snapshot("tencent", "quote", "quotes")
        assert state.consecutive_failures == 0
        assert state.successes == 1
        assert state.circuit_open is False
    finally:
        service.close()


def test_reset_clears_open_provider_circuit() -> None:
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    health.record_failure("tencent", "quote", "quotes", SourceUnavailable("down"))
    assert health.snapshot("tencent", "quote", "quotes").circuit_open is True
    health.reset("tencent")
    assert health.snapshot("tencent", "quote", "quotes").circuit_open is False
