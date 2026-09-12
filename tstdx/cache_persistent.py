# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Safe persistent semantic L2 cache for provider-first runtime.

The persistent format is JSON + SQLite; it never unpickles executable objects.
Only DIRECT, non-fallback QueryResults with exact plan identity are persisted.
Every read reconstructs a SemanticCacheEntry and re-runs the canonical
fingerprint/identity/TTL/freshness checks before returning or promoting data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .cache_semantic import SEMANTIC_CACHE_SCHEMA_VERSION, SemanticCacheEntry
from .domain.models import Bar, Level, Quote
from .query import QueryPlan
from .result import Provenance, ProvenanceKind, QueryResult

__all__ = ["PersistentSemanticCache"]

_CODEC_VERSION = 1


def _encode(value: Any) -> Any:
    if isinstance(value, Quote):
        return {"__tstdx_type__": "Quote", "value": asdict(value)}
    if isinstance(value, Bar):
        return {"__tstdx_type__": "Bar", "value": asdict(value)}
    if isinstance(value, Level):
        return {"__tstdx_type__": "Level", "value": asdict(value)}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, tuple):
        return {"__tstdx_type__": "tuple", "value": [_encode(item) for item in value]}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("persistent semantic cache requires string dict keys")
        return {
            "__tstdx_type__": "dict",
            "value": {key: _encode(item) for key, item in value.items()},
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported persistent cache value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    type_name = value.get("__tstdx_type__")
    payload = value.get("value")
    if type_name == "dict":
        if not isinstance(payload, dict):
            raise ValueError("invalid dict payload")
        return {key: _decode(item) for key, item in payload.items()}
    if type_name == "tuple":
        if not isinstance(payload, list):
            raise ValueError("invalid tuple payload")
        return tuple(_decode(item) for item in payload)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid {type_name} payload")
    if type_name == "Bar":
        return Bar(**payload)
    if type_name == "Level":
        return Level(**payload)
    if type_name == "Quote":
        data = dict(payload)
        data["bid"] = [Level(**item) for item in data.get("bid", [])]
        data["ask"] = [Level(**item) for item in data.get("ask", [])]
        return Quote(**data)
    raise ValueError(f"unknown persistent cache type tag: {type_name!r}")


class PersistentSemanticCache:
    """SQLite-backed L2 that accepts only verified DIRECT provenance."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_cache_v1 (
                fingerprint TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                codec_version INTEGER NOT NULL,
                provider TEXT NOT NULL,
                channel TEXT NOT NULL,
                capability TEXT NOT NULL,
                stored_at_ns INTEGER NOT NULL,
                expires_at_ns INTEGER,
                provenance_json TEXT NOT NULL,
                data_json TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self) -> "PersistentSemanticCache":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def _persistent_allowed(result: QueryResult[Any]) -> bool:
        provenance = result.meta.provenance
        return provenance.kind is ProvenanceKind.DIRECT and not provenance.fallback

    def put(
        self,
        plan: QueryPlan,
        result: QueryResult[Any],
        *,
        ttl: float | None,
        now_ns: int | None = None,
    ) -> bool:
        expected = (plan.provider, plan.channel, plan.spec.capability, plan.fingerprint.value)
        actual = (
            result.meta.provider,
            result.meta.channel,
            result.meta.capability,
            result.meta.fingerprint,
        )
        if actual != expected:
            raise ValueError("persistent cache result identity does not match QueryPlan")
        if not self._persistent_allowed(result):
            return False

        entry = SemanticCacheEntry.from_result(result, ttl=ttl, now_ns=now_ns)
        provenance = entry.provenance
        provenance_json = json.dumps(
            {
                "fingerprint": entry.fingerprint,
                "provider": provenance.provider,
                "channel": provenance.channel,
                "capability": provenance.capability,
                "kind": provenance.kind.value,
                "observed_at_ns": provenance.observed_at_ns,
                "provider_timestamp": provenance.provider_timestamp,
                "requested_provider": provenance.requested_provider,
                "fallback": provenance.fallback,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        data_json = json.dumps(
            _encode(entry.data),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock:
            self._db.execute(
                """
                INSERT OR REPLACE INTO semantic_cache_v1
                (fingerprint, schema_version, codec_version, provider, channel,
                 capability, stored_at_ns, expires_at_ns, provenance_json, data_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.fingerprint,
                    entry.schema_version,
                    _CODEC_VERSION,
                    entry.provider,
                    entry.channel,
                    entry.capability,
                    entry.stored_at_ns,
                    entry.expires_at_ns,
                    provenance_json,
                    data_json,
                ),
            )
            self._db.commit()
        return True

    def get_with_ttl(
        self,
        plan: QueryPlan,
        *,
        now_ns: int | None = None,
    ) -> tuple[QueryResult[Any], float | None] | None:
        now = time.time_ns() if now_ns is None else int(now_ns)
        key = plan.fingerprint.value
        try:
            with self._lock:
                row = self._db.execute(
                    """
                    SELECT schema_version, codec_version, provider, channel, capability,
                           stored_at_ns, expires_at_ns, provenance_json, data_json
                    FROM semantic_cache_v1 WHERE fingerprint = ?
                    """,
                    (key,),
                ).fetchone()
            if row is None:
                return None
            (
                schema_version,
                codec_version,
                provider,
                channel,
                capability,
                stored_at_ns,
                expires_at_ns,
                provenance_json,
                data_json,
            ) = row
            if codec_version != _CODEC_VERSION or schema_version != SEMANTIC_CACHE_SCHEMA_VERSION:
                self.invalidate(plan)
                return None
            prov_data = json.loads(provenance_json)
            if prov_data.get("fingerprint") != key:
                self.invalidate(plan)
                return None
            provenance = Provenance(
                provider=str(prov_data["provider"]),
                channel=str(prov_data["channel"]),
                capability=str(prov_data["capability"]),
                kind=ProvenanceKind(str(prov_data["kind"])),
                observed_at_ns=int(prov_data["observed_at_ns"]),
                provider_timestamp=prov_data.get("provider_timestamp"),
                requested_provider=prov_data.get("requested_provider"),
                fallback=bool(prov_data.get("fallback", False)),
            )
            if provenance.kind is not ProvenanceKind.DIRECT or provenance.fallback:
                self.invalidate(plan)
                return None
            entry = SemanticCacheEntry(
                schema_version=int(schema_version),
                fingerprint=key,
                provider=str(provider),
                channel=str(channel),
                capability=str(capability),
                stored_at_ns=int(stored_at_ns),
                expires_at_ns=None if expires_at_ns is None else int(expires_at_ns),
                data=_decode(json.loads(data_json)),
                provenance=provenance,
            )
            if not entry.matches(plan, now_ns=now):
                self.invalidate(plan)
                return None
            remaining: float | None = None
            if entry.expires_at_ns is not None:
                remaining = max(0.0, (entry.expires_at_ns - now) / 1_000_000_000)
            return entry.to_result(plan, cache_tier="l2"), remaining
        except Exception:
            self.invalidate(plan)
            return None

    def get(self, plan: QueryPlan, *, now_ns: int | None = None) -> QueryResult[Any] | None:
        hit = self.get_with_ttl(plan, now_ns=now_ns)
        return None if hit is None else hit[0]

    def invalidate(self, plan: QueryPlan) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM semantic_cache_v1 WHERE fingerprint = ?",
                (plan.fingerprint.value,),
            )
            self._db.commit()
            return cursor.rowcount > 0

    def clear(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM semantic_cache_v1").fetchone()
            count = int(row[0]) if row else 0
            self._db.execute("DELETE FROM semantic_cache_v1")
            self._db.commit()
            return count
