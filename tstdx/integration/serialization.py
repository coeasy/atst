# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Shared transport serialization for canonical v13 result contracts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from ..runtime.orchestration import OrchestratedResult

__all__ = ["jsonable", "serialize_result"]


def jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if hasattr(value, "to_dict"):
        return jsonable(value.to_dict())
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return value


def _query_result(result: Any) -> dict[str, Any]:
    meta = result.meta
    provenance = meta.provenance
    return {
        "data": jsonable(result.data),
        "meta": {
            "provider": meta.provider,
            "channel": meta.channel,
            "capability": meta.capability,
            "fingerprint": meta.fingerprint,
            "provenance": {
                "kind": provenance.kind.value,
                "observed_at_ns": provenance.observed_at_ns,
                "cache_tier": provenance.cache_tier,
                "fallback": provenance.fallback,
                "requested_provider": provenance.requested_provider,
            },
        },
    }


def serialize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, OrchestratedResult):
        payload = _query_result(result.result)
        payload["attempts"] = [jsonable(item) for item in result.attempts]
        return payload
    return _query_result(result)
