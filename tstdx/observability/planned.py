# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Low-cardinality metrics for the v12 planned runtime.

Never use symbol, query id, URL, host, raw error text or request id as labels.
Those belong in structured logs/traces. Metrics record only stable dimensions.
All helpers are fail-open: observability must never change business semantics.
"""

from __future__ import annotations

import contextlib

from .metrics import Counter, Gauge, Histogram, metrics

__all__ = [
    "record_planned_query",
    "record_cache_event",
    "record_singleflight",
    "record_batch_chunks",
    "record_provider_health",
    "record_stream_gap",
]

_QUERY_TOTAL = metrics.registry.register(
    Counter(
        "tstdx_planned_query_total",
        "Planned service logical queries",
        labelnames=("provider", "channel", "capability", "status"),
    )
)
_QUERY_DURATION = metrics.registry.register(
    Histogram(
        "tstdx_planned_query_duration_seconds",
        "Planned service logical query latency",
        labelnames=("provider", "channel", "capability"),
    )
)
_CACHE_TOTAL = metrics.registry.register(
    Counter(
        "tstdx_semantic_cache_total",
        "Semantic cache events",
        labelnames=("layer", "status"),
    )
)
_SINGLEFLIGHT_TOTAL = metrics.registry.register(
    Counter(
        "tstdx_singleflight_total",
        "SingleFlight leader/join events",
        labelnames=("event",),
    )
)
_BATCH_CHUNKS = metrics.registry.register(
    Histogram(
        "tstdx_provider_batch_chunks",
        "Provider chunks per logical batch query",
        labelnames=("provider", "channel", "capability"),
        buckets=(1, 2, 3, 5, 10, 20, 50),
    )
)
_PROVIDER_HEALTH = metrics.registry.register(
    Gauge(
        "tstdx_provider_health",
        "Current Provider/Channel/Capability health state (1 healthy, 0 open)",
        labelnames=("provider", "channel", "capability"),
    )
)
_STREAM_GAPS = metrics.registry.register(
    Counter(
        "tstdx_stream_gap_total",
        "Detected stream gaps",
        labelnames=("provider", "kind"),
    )
)


def record_planned_query(
    *,
    provider: str,
    channel: str,
    capability: str,
    status: str,
    duration: float | None = None,
) -> None:
    with contextlib.suppress(Exception):
        labels = {
            "provider": provider,
            "channel": channel,
            "capability": capability,
            "status": status,
        }
        _QUERY_TOTAL.inc(labels=labels)
        if duration is not None:
            _QUERY_DURATION.observe(
                max(duration, 0.0),
                labels={
                    "provider": provider,
                    "channel": channel,
                    "capability": capability,
                },
            )


def record_cache_event(*, layer: str, status: str) -> None:
    with contextlib.suppress(Exception):
        _CACHE_TOTAL.inc(labels={"layer": layer, "status": status})


def record_singleflight(event: str) -> None:
    if event not in {"leader", "join", "timeout"}:
        return
    with contextlib.suppress(Exception):
        _SINGLEFLIGHT_TOTAL.inc(labels={"event": event})


def record_batch_chunks(
    *, provider: str, channel: str, capability: str, chunks: int
) -> None:
    with contextlib.suppress(Exception):
        _BATCH_CHUNKS.observe(
            max(float(chunks), 0.0),
            labels={
                "provider": provider,
                "channel": channel,
                "capability": capability,
            },
        )


def record_provider_health(
    *, provider: str, channel: str, capability: str, healthy: bool
) -> None:
    with contextlib.suppress(Exception):
        _PROVIDER_HEALTH.set(
            1.0 if healthy else 0.0,
            labels={
                "provider": provider,
                "channel": channel,
                "capability": capability,
            },
        )


def record_stream_gap(*, provider: str, kind: str) -> None:
    with contextlib.suppress(Exception):
        _STREAM_GAPS.inc(labels={"provider": provider, "kind": kind})
