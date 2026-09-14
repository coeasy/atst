# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-bound parity tests for the v12 compatibility router.

The historical test compared two five-level fallback chains. That architecture
has been removed. These tests now lock the important parity invariant: legacy
Router selectors and the canonical service resolve to exactly one Provider and
never change provenance after an error.
"""

from __future__ import annotations

from typing import Any

import pytest

from tstdx.errors import SourceUnavailable, ValidationError
from tstdx.service import QueryResult
from tstdx.sources import DataSourceRouter


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.quote_rows: list[Any] = [
            {
                "code": "sh600519",
                "price": 10.0,
                "last_close": 9.9,
                "open": 9.95,
                "high": 10.2,
                "low": 9.9,
                "volume": 100,
                "amount": 1000.0,
            }
        ]
        self.bar_rows: list[Any] = [
            {
                "datetime": "2026-09-08 15:00",
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.1,
                "volume": 100,
                "amount": 1000.0,
            }
        ]
        self.fail_provider: str | None = None
        self.closed = False

    def quotes(self, symbols, *, provider=None, source=None, with_meta=False):  # noqa: ANN001
        self.calls.append(("quotes", provider or source or "tdx", {"symbols": list(symbols)}))
        if (provider or source) == self.fail_provider:
            raise SourceUnavailable(
                f"Provider {self.fail_provider!r} unavailable",
                context={"provider": self.fail_provider, "fallback": False},
            )
        if not self.quote_rows:
            raise SourceUnavailable(
                f"Provider {provider!r} 返回空实时行情",
                context={"provider": provider, "fallback": False},
            )
        return list(self.quote_rows)

    def bars(
        self,
        symbol,
        *,
        period="day",
        count=320,
        start=0,
        adjust="",
        provider=None,
        source=None,
        with_meta=False,
    ):  # noqa: ANN001
        self.calls.append(
            (
                "bars",
                provider or source or "tdx",
                {
                    "symbol": symbol,
                    "period": period,
                    "count": count,
                    "start": start,
                    "adjust": adjust,
                },
            )
        )
        if (provider or source) == self.fail_provider:
            raise SourceUnavailable(
                f"Provider {self.fail_provider!r} unavailable",
                context={"provider": self.fail_provider, "fallback": False},
            )
        if not self.bar_rows:
            raise SourceUnavailable(
                f"Provider {provider!r} 返回空 K 线",
                context={"provider": provider, "fallback": False},
            )
        return list(self.bar_rows)

    def close(self) -> None:
        self.closed = True


def _router(service: FakeService, **kwargs: Any) -> DataSourceRouter:
    return DataSourceRouter(service=service, **kwargs)  # type: ignore[arg-type]


class TestProviderSelectionParity:
    def test_default_is_tdx(self) -> None:
        service = FakeService()
        rows = _router(service).quotes(["sh600519"])
        assert rows[0]["code"] == "sh600519"
        assert service.calls == [("quotes", "tdx", {"symbols": ["sh600519"]})]

    def test_explicit_provider_is_forwarded_exactly_once(self) -> None:
        service = FakeService()
        _router(service).quotes(["sh600519"], provider="sina")
        assert [call[1] for call in service.calls] == ["sina"]

    def test_source_alias_selects_same_provider(self) -> None:
        service = FakeService()
        _router(service).quotes(["sh600519"], source="tencent")
        assert [call[1] for call in service.calls] == ["tencent"]

    def test_conflicting_provider_source_fails_before_execution(self) -> None:
        service = FakeService()
        router = _router(service)
        with pytest.raises(ValidationError):
            router.quotes(["sh600519"], provider="tdx", source="sina")
        assert service.calls == []

    def test_legacy_web_selects_one_configured_provider(self) -> None:
        service = FakeService()
        router = _router(service, web_sources=["sina", "tencent"])
        router.quotes(["sh600519"], order=["web"])
        assert [call[1] for call in service.calls] == ["sina"]

    def test_multi_order_is_rejected_not_interpreted_as_fallback(self) -> None:
        service = FakeService()
        router = _router(service)
        with pytest.raises(ValidationError):
            router.quotes(["sh600519"], order=["tdx", "web"])
        assert service.calls == []


class TestFailureParity:
    def test_tdx_failure_does_not_call_another_provider(self) -> None:
        service = FakeService()
        service.fail_provider = "tdx"
        router = _router(service, web_sources=["sina", "tencent"])

        with pytest.raises(SourceUnavailable) as caught:
            router.quotes(["sh600519"])

        assert caught.value.context["provider"] == "tdx"
        assert caught.value.context["fallback"] is False
        assert [call[1] for call in service.calls] == ["tdx"]
        assert router.last_source is None
        assert len(router.last_errors) == 1

    def test_explicit_sina_failure_stays_sina(self) -> None:
        service = FakeService()
        service.fail_provider = "sina"
        router = _router(service)

        with pytest.raises(SourceUnavailable):
            router.quotes(["sh600519"], provider="sina")

        assert [call[1] for call in service.calls] == ["sina"]

    def test_default_empty_ok_only_converts_provider_empty_result(self) -> None:
        service = FakeService()
        service.quote_rows = []
        rows = _router(service).quotes(["sh600519"], default_empty_ok=True)
        assert rows == []
        assert [call[1] for call in service.calls] == ["tdx"]


class TestBarsParity:
    def test_tdx_window_arguments_are_preserved(self) -> None:
        service = FakeService()
        router = _router(service)
        router.kline("sh600519", period="day", count=5, start=2, provider="tdx")

        assert service.calls == [
            (
                "bars",
                "tdx",
                {
                    "symbol": "sh600519",
                    "period": "day",
                    "count": 5,
                    "start": 2,
                    "adjust": "",
                },
            )
        ]

    def test_non_default_provider_is_preserved(self) -> None:
        service = FakeService()
        router = _router(service)
        router.kline("sh600519", provider="eastmoney", adjust="qfq")
        assert service.calls[0][1] == "eastmoney"
        assert service.calls[0][2]["adjust"] == "qfq"

    def test_tdx_non_quotation_channel_requires_direct_api(self) -> None:
        service = FakeService()
        router = _router(service)
        with pytest.raises(ValidationError, match="md.tdx"):
            router.kline("hk00700", provider="tdx", channel="extended")
        assert service.calls == []


def test_external_service_lifecycle_is_not_owned_by_router() -> None:
    service = FakeService()
    router = _router(service)
    router.close()
    assert service.closed is False


def test_query_result_type_remains_available_for_service_contract() -> None:
    """Guard import surface used by provider-aware integrations."""
    assert QueryResult.__name__ == "QueryResult"
