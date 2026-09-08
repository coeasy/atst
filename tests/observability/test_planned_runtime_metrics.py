from __future__ import annotations

from tstdx.observability.metrics import Counter, Gauge, Histogram, metrics
from tstdx.observability.planned import (
    record_batch_chunks,
    record_cache_event,
    record_planned_query,
    record_provider_health,
    record_singleflight,
    record_stream_gap,
)


def _metric(name: str):  # noqa: ANN201
    value = metrics.registry.get(name)
    assert value is not None
    return value


def test_planned_metrics_use_only_low_cardinality_labels() -> None:
    expected = {
        "tstdx_planned_query_total": ("provider", "channel", "capability", "status"),
        "tstdx_planned_query_duration_seconds": ("provider", "channel", "capability"),
        "tstdx_semantic_cache_total": ("layer", "status"),
        "tstdx_singleflight_total": ("event",),
        "tstdx_provider_batch_chunks": ("provider", "channel", "capability"),
        "tstdx_provider_health": ("provider", "channel", "capability"),
        "tstdx_stream_gap_total": ("provider", "kind"),
    }
    forbidden = {"symbol", "query_id", "request_id", "url", "host", "error", "message"}
    for name, labels in expected.items():
        metric = _metric(name)
        assert metric.labelnames == labels
        assert forbidden.isdisjoint(metric.labelnames)


def test_planned_counter_and_gauge_helpers_increment_without_business_side_effects() -> None:
    query = _metric("tstdx_planned_query_total")
    cache = _metric("tstdx_semantic_cache_total")
    singleflight = _metric("tstdx_singleflight_total")
    health = _metric("tstdx_provider_health")
    gaps = _metric("tstdx_stream_gap_total")
    assert isinstance(query, Counter)
    assert isinstance(cache, Counter)
    assert isinstance(singleflight, Counter)
    assert isinstance(health, Gauge)
    assert isinstance(gaps, Counter)

    query_labels = {
        "provider": "tdx",
        "channel": "quotation",
        "capability": "quotes",
        "status": "ok",
    }
    cache_labels = {"layer": "memory", "status": "hit"}
    singleflight_labels = {"event": "leader"}
    health_labels = {
        "provider": "tdx",
        "channel": "quotation",
        "capability": "quotes",
    }
    gap_labels = {"provider": "tdx", "kind": "detected"}

    before_query = query.value(query_labels)
    before_cache = cache.value(cache_labels)
    before_flight = singleflight.value(singleflight_labels)
    before_gap = gaps.value(gap_labels)

    record_planned_query(
        provider="tdx",
        channel="quotation",
        capability="quotes",
        status="ok",
        duration=0.001,
    )
    record_cache_event(layer="memory", status="hit")
    record_singleflight("leader")
    record_provider_health(
        provider="tdx",
        channel="quotation",
        capability="quotes",
        healthy=False,
    )
    record_stream_gap(provider="tdx", kind="detected")

    assert query.value(query_labels) == before_query + 1
    assert cache.value(cache_labels) == before_cache + 1
    assert singleflight.value(singleflight_labels) == before_flight + 1
    assert health.value(health_labels) == 0.0
    assert gaps.value(gap_labels) == before_gap + 1


def test_batch_chunk_histogram_records_one_observation() -> None:
    metric = _metric("tstdx_provider_batch_chunks")
    assert isinstance(metric, Histogram)
    labels = {"provider": "tdx", "channel": "quotation", "capability": "quotes"}
    key = metric._key(labels)
    with metric._lock:
        before = metric._series.get(key, {}).get("_count", 0.0)

    record_batch_chunks(
        provider="tdx",
        channel="quotation",
        capability="quotes",
        chunks=3,
    )

    with metric._lock:
        after = metric._series[key]["_count"]
    assert after == before + 1
