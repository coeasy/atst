from __future__ import annotations

import inspect

import pytest

import tstdx
from tstdx.async_service import UnifiedMarketDataService as AsyncSyncService
from tstdx.batch import BatchResult
from tstdx.failure import FailureDisposition
from tstdx.facade import AsyncUnifiedQuoteAPI, UnifiedMarketDataService, UnifiedQuoteAPI
from tstdx.health import SourceHealthRegistry
from tstdx.integration import create_app
from tstdx.integration.http_app import PlannedProviderHttpClient, PlannedTaskStore
from tstdx.integration.tasks import TaskStore as SafeTaskStore
from tstdx.planned_service import UnifiedMarketDataService as PlannedService
from tstdx.streaming.planned import PlannedQuoteStream, StreamWatermark


def test_top_level_service_and_query_contracts_point_to_planned_modules() -> None:
    assert tstdx._LAZY["UnifiedMarketDataService"] == (
        "tstdx.planned_service",
        "UnifiedMarketDataService",
    )
    assert tstdx._LAZY["market_data"] == ("tstdx.planned_service", "market_data")
    assert tstdx._LAZY["BatchResult"] == ("tstdx.batch", "BatchResult")
    assert tstdx._LAZY["SourceHealthRegistry"] == (
        "tstdx.health",
        "SourceHealthRegistry",
    )
    assert tstdx._LAZY["PlannedQuoteStream"] == (
        "tstdx.streaming.planned",
        "PlannedQuoteStream",
    )
    assert tstdx._LAZY["StreamWatermark"] == (
        "tstdx.streaming.planned",
        "StreamWatermark",
    )
    assert tstdx.UnifiedMarketDataService is PlannedService
    assert tstdx.BatchResult is BatchResult
    assert tstdx.SourceHealthRegistry is SourceHealthRegistry
    assert tstdx.PlannedQuoteStream is PlannedQuoteStream
    assert tstdx.StreamWatermark is StreamWatermark


def test_official_facades_do_not_export_legacy_routing_classes() -> None:
    assert UnifiedMarketDataService is PlannedService
    assert UnifiedQuoteAPI.__module__ == "tstdx.facade.planned"
    assert AsyncUnifiedQuoteAPI.__module__ == "tstdx.facade.strict_async"


def test_async_market_data_uses_same_planned_sync_service() -> None:
    assert AsyncSyncService is PlannedService


def test_package_http_factory_delegates_to_planned_http_app() -> None:
    source = inspect.getsource(create_app)
    assert ".http_app" in source
    assert "http_server" not in source
    assert issubclass(PlannedTaskStore, SafeTaskStore)

    client = PlannedProviderHttpClient()
    try:
        assert client._service_factory is PlannedService
    finally:
        client.close()


def test_failure_disposition_cannot_be_constructed_with_provider_switch() -> None:
    with pytest.raises(TypeError):
        FailureDisposition(
            retry_same_provider=False,
            switch_host=False,
            retry_after=None,
            terminal=True,
            reason="test",
            provider_switch_allowed=True,
        )
