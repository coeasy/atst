from __future__ import annotations

from typing import Any

import pytest

from tstdx.errors import ValidationError
from tstdx.service import UnifiedMarketDataService


class FakeChannelAdapter:
    def __init__(self, provider: str, channel: str) -> None:
        self.provider = provider
        self.channel = channel
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _call(self, method: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((method, args, kwargs))
        return [{"provider": self.provider, "channel": self.channel, "method": method}]

    def fetch_minute(self, symbol: str):
        return self._call("fetch_minute", symbol)

    def fetch_ticks(self, symbol: str, *, max_pages: int = 1, limit: int = 200):
        return self._call("fetch_ticks", symbol, max_pages=max_pages, limit=limit)

    def fetch_boards(self, board: str = "industry", *, limit: int = 20, page: int = 1):
        return self._call("fetch_boards", board, limit=limit, page=page)

    def fetch_suggest(self, key: str, *, limit: int = 10):
        return self._call("fetch_suggest", key, limit=limit)

    def fetch_news(self, symbol: str, *, page: int = 1, num: int = 20, tag=None):
        return self._call("fetch_news", symbol, page=page, num=num, tag=tag)

    def fetch_flow(self, symbols):
        return self._call("fetch_flow", tuple(symbols))

    def fetch_hot_rank(self, *, page: int = 1, size: int = 100):
        return self._call("fetch_hot_rank", page=page, size=size)

    def fetch_strategy(self, query: str, *, page: int = 1, limit: int = 50):
        return self._call("fetch_strategy", query, page=page, limit=limit)

    def fetch_rates(self):
        return self._call("fetch_rates")

    def fetch(self, symbols):
        return self._call("fetch", tuple(symbols))


class FakeManager:
    def __init__(self) -> None:
        self.timeout = 5.0
        self.adapters: dict[tuple[str, str, str], FakeChannelAdapter] = {}
        self.web_requests: list[tuple[str, str, str]] = []

    def web_adapter(
        self,
        provider: str,
        channel: str,
        adapter_cls: type[Any],
        *,
        resource_key: str | None = None,
        **kwargs: Any,
    ) -> FakeChannelAdapter:
        key = (provider, channel, resource_key or channel)
        self.web_requests.append(key)
        if key not in self.adapters:
            self.adapters[key] = FakeChannelAdapter(provider, channel)
        return self.adapters[key]

    def tdx_channel(self, channel: str) -> Any:
        raise AssertionError(f"unexpected TDX channel access: {channel}")

    def close(self) -> None:
        pass


def _service() -> tuple[UnifiedMarketDataService, FakeManager]:
    manager = FakeManager()
    return UnifiedMarketDataService(manager=manager), manager  # type: ignore[arg-type]


def test_tencent_minute_is_bound_to_tencent_minute_channel() -> None:
    service, manager = _service()
    rows = service.tencent.minute("sh600519")
    assert rows[0]["provider"] == "tencent"
    assert rows[0]["channel"] == "minute"
    assert manager.web_requests == [("tencent", "minute", "minute")]


def test_sina_news_never_resolves_another_provider() -> None:
    service, manager = _service()
    rows = service.sina.news("sh600519", num=5)
    assert rows[0]["provider"] == "sina"
    assert manager.web_requests == [("sina", "news", "news")]


def test_eastmoney_specific_capability_keeps_provider_identity() -> None:
    service, manager = _service()
    rows = service.eastmoney.hot_rank(size=20)
    assert rows[0]["provider"] == "eastmoney"
    assert rows[0]["channel"] == "hot_rank"
    assert manager.web_requests == [("eastmoney", "hot_rank", "hot_rank")]


def test_iwencai_screening_is_not_modeled_as_quote_source() -> None:
    service, manager = _service()
    rows = service.iwencai.screen("连续三年ROE大于15%")
    assert rows[0]["provider"] == "iwencai"
    assert rows[0]["channel"] == "screening"
    assert manager.web_requests == [("iwencai", "screening", "screening")]


def test_boc_fx_is_provider_specific_channel() -> None:
    service, manager = _service()
    rows = service.boc.fx_rates()
    assert rows[0]["provider"] == "boc"
    assert rows[0]["channel"] == "fx"
    assert manager.web_requests == [("boc", "fx", "fx")]


def test_provider_cannot_open_channel_owned_by_another_provider() -> None:
    service, _ = _service()
    with pytest.raises(ValidationError):
        service.tencent.channel("fund_flow")


def test_tdx_vipdoc_is_not_exposed_as_live_network_channel() -> None:
    service, _ = _service()
    with pytest.raises(ValidationError, match="local_historical"):
        service.tdx.channel("vipdoc")
