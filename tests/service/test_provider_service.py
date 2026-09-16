from __future__ import annotations

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import AllHostsUnreachable, SourceUnavailable, ValidationError
from tstdx.service import QueryResult, UnifiedMarketDataService


class FailingTdx:
    def quotes(self, *args, **kwargs):
        raise AllHostsUnreachable("all tdx hosts failed")


class FakeQuoteAdapter:
    """Mirrors the current per-Provider quote adapter surface (`.fetch` / `.fetch_quote`)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def _row(self, symbol: str) -> Quote:
        return Quote(
            code=symbol,
            price=10.0,
            last_close=9.9,
            open=9.95,
            high=10.1,
            low=9.8,
            volume=100,
            amount=1000.0,
        )

    def fetch(self, symbols):
        return [self._row(symbols[0])]

    def fetch_quote(self, symbol):
        return self._row(symbol)


class FakeManager:
    def __init__(self) -> None:
        self.tdx = FailingTdx()
        #: 记录被请求过的 web Provider（`quote_adapter` 即 Provider 级 web adapter 工厂）
        self.web_calls: list[str] = []

    def quote_adapter(self, provider: str) -> FakeQuoteAdapter:
        self.web_calls.append(provider)
        return FakeQuoteAdapter(provider)

    def close(self) -> None:
        pass


def test_tdx_failure_does_not_call_any_web_provider() -> None:
    manager = FakeManager()
    service = UnifiedMarketDataService(manager=manager)

    with pytest.raises(SourceUnavailable) as caught:
        service.quotes("sh600519")

    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["fallback"] is False
    assert manager.web_calls == []


def test_explicit_tencent_query_is_provider_bound_and_has_provenance() -> None:
    manager = FakeManager()
    service = UnifiedMarketDataService(manager=manager)

    result = service.quotes("sh600519", provider="tencent", with_meta=True)

    assert isinstance(result, QueryResult)
    assert result.meta.provider == "tencent"
    assert result.meta.channel == "quote"
    assert result.meta.real is True
    assert result.meta.fallback is False
    assert manager.web_calls == ["tencent"]


def test_source_alias_resolves_to_same_provider() -> None:
    manager = FakeManager()
    service = UnifiedMarketDataService(manager=manager)

    result = service.quotes("sh600519", source="sina", with_meta=True)

    assert result.meta.provider == "sina"
    assert manager.web_calls == ["sina"]


def test_provider_source_conflict_fails_before_execution() -> None:
    manager = FakeManager()
    service = UnifiedMarketDataService(manager=manager)

    with pytest.raises(ValidationError):
        service.quotes("sh600519", provider="tdx", source="sina")

    assert manager.web_calls == []


def test_tdx_direct_namespace_is_explicit_channel() -> None:
    manager = FakeManager()
    service = UnifiedMarketDataService(manager=manager)

    assert service.tdx.provider == "tdx"
    assert service.tdx.quotation is not None
