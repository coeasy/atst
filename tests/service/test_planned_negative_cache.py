from __future__ import annotations

import pytest

from tstdx.errors import CommandOffline
from tstdx.execution import SingleFlight
from tstdx.planned_service import UnifiedMarketDataService


class FailingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbols):  # noqa: ANN001,ANN201
        self.calls += 1
        raise CommandOffline(
            "command offline",
            context={"command": "test", "fallback": False},
        )


class Manager:
    def __init__(self) -> None:
        self.adapters = {
            "tencent": FailingAdapter(),
            "sina": FailingAdapter(),
        }

    def quote_adapter(self, provider: str) -> FailingAdapter:
        return self.adapters[provider]

    def close(self) -> None:
        pass


def test_planned_service_coalesces_stable_terminal_error_for_same_fingerprint() -> None:
    manager = Manager()
    service = UnifiedMarketDataService(
        manager=manager,
        singleflight=SingleFlight(negative_ttl=1.0),
    )
    try:
        for _ in range(2):
            with pytest.raises(CommandOffline) as caught:
                service.quotes(["sh600519"], provider="tencent")
            assert caught.value.context["provider"] == "tencent"
            assert caught.value.context["channel"] == "quote"
            assert caught.value.context["capability"] == "quotes"
            assert caught.value.context["fallback"] is False

        assert manager.adapters["tencent"].calls == 1
    finally:
        service.close()


def test_negative_cache_key_is_provider_scoped_at_planned_service_boundary() -> None:
    manager = Manager()
    service = UnifiedMarketDataService(
        manager=manager,
        singleflight=SingleFlight(negative_ttl=1.0),
    )
    try:
        with pytest.raises(CommandOffline):
            service.quotes(["sh600519"], provider="tencent")
        with pytest.raises(CommandOffline):
            service.quotes(["sh600519"], provider="sina")

        assert manager.adapters["tencent"].calls == 1
        assert manager.adapters["sina"].calls == 1
    finally:
        service.close()
