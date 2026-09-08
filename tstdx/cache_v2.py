# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Semantic Cache V2: exact fingerprint L1/L2 caching without pickle.

Only canonical ``QueryResult[list[Quote|Bar]]`` values are persisted. SQLite
storage failures are optimization failures: they log and degrade to cache miss,
never changing the upstream query result.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .domain.models import BAR_FIELDS, QUOTE_FIELDS, Bar, Level, Quote
from .freshness import FreshnessMode, FreshnessStatus
from .semantic_cache import SemanticQueryCache
from .service import FreshnessEvidence, QueryResult, ResultMeta

__all__ = ["SQLiteSemanticQueryCache", "TieredSemanticQueryCache"]

_LOG = logging.getLogger(__name__)
_FORMAT_VERSION = 1


def _quote_from_dict(row: dict[str, Any]) -> Quote:
    core = {key: row.get(key) for key in QUOTE_FIELDS if key not in {"bid", "ask"}}
    core["bid"] = [Level(**item) for item in row.get("bid", []) if isinstance(item, dict)]
    core["ask"] = [Level(**item) for item in row.get("ask", []) if isinstance(item, dict)]
    extra = {key: value for key, value in row.items() if key not in QUOTE_FIELDS}
    core["extra"] = extra
    return Quote(**core)


def _encode_meta(meta: ResultMeta) -> dict[str, Any]:
    status = meta.freshness_status
    return {
        "provider": meta.provider,
        "channel": meta.channel,
        "capability": meta.capability,
        "observed_at_ns": meta.observed_at_ns,
        "real": meta.real,
        "fallback": meta.fallback,
        "freshness": {
            "origin": meta.freshness.origin,
            "observed_at_ns": meta.freshness.observed_at_ns,
            "provider_timestamp": meta.freshness.provider_timestamp,
            "cache_hit": meta.freshness.cache_hit,
            "replay": meta.freshness.replay,
            "synthetic": meta.freshness.synthetic,
        },
        "freshness_status": None
        if status is None
        else {
            "verified": status.verified,
            "mode": status.mode.value,
            "basis": status.basis,
            "provider_timestamp": status.provider_timestamp,
            "observed_age_seconds": status.observed_age_seconds,
            "currentness_verified": status.currentness_verified,
        },
    }


def _decode_meta(data: dict[str, Any]) -> ResultMeta:
    fresh = data["freshness"]
    status_data = data.get("freshness_status")
    status = None
    if isinstance(status_data, dict):
        status = FreshnessStatus(
            verified=bool(status_data["verified"]),
            mode=FreshnessMode(status_data["mode"]),
            basis=str(status_data["basis"]),
            provider_timestamp=status_data.get("provider_timestamp"),
            observed_age_seconds=float(status_data["observed_age_seconds"]),
            currentness_verified=bool(status_data["currentness_verified"]),
        )
    return ResultMeta(
        provider=str(data["provider"]),
        channel=str(data["channel"]),
        capability=str(data["capability"]),
        observed_at_ns=int(data["observed_at_ns"]),
        freshness=FreshnessEvidence(
            origin=str(fresh["origin"]),
            observed_at_ns=int(fresh["observed_at_ns"]),
            provider_timestamp=fresh.get("provider_timestamp"),
            cache_hit=bool(fresh.get("cache_hit", False)),
            replay=bool(fresh.get("replay", False)),
            synthetic=bool(fresh.get("synthetic", False)),
        ),
        freshness_status=status,
        real=bool(data.get("real", True)),
        fallback=bool(data.get("fallback", False)),
    )


def _encode_result(value: Any) -> str | None:
    if not isinstance(value, QueryResult):
        return None
    capability = value.meta.capability
    if capability not in {"quotes", "bars"} or not isinstance(value.data, list):
        return None
    if capability == "quotes":
        if not all(isinstance(item, Quote) for item in value.data):
            return None
        rows = [item.to_dict() for item in value.data]
    else:
        if not all(isinstance(item, Bar) for item in value.data):
            return None
        rows = [item.to_dict() for item in value.data]
    payload = {
        "format_version": _FORMAT_VERSION,
        "kind": capability,
        "data": rows,
        "meta": _encode_meta(value.meta),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_result(payload: str) -> QueryResult[Any]:
    data = json.loads(payload)
    if int(data.get("format_version", -1)) != _FORMAT_VERSION:
        raise ValueError("unsupported semantic cache format")
    kind = data.get("kind")
    rows = data.get("data")
    if not isinstance(rows, list):
        raise ValueError("invalid semantic cache data")
    if kind == "quotes":
        items = [_quote_from_dict(dict(row)) for row in rows]
    elif kind == "bars":
        items = [Bar.from_dict(dict(row)) for row in rows]
    else:
        raise ValueError("unsupported semantic cache kind")
    return QueryResult(data=items, meta=_decode_meta(dict(data["meta"])))


class SQLiteSemanticQueryCache:
    """Persistent exact-fingerprint semantic cache.

    Age uses wall clock because monotonic timestamps are not portable across
    process restarts. The decoded result still carries its original observation
    timestamp and is revalidated by the planned service before delivery.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path).expanduser())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_cache (
                fingerprint TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                stored_wall_ns INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.errors = 0

    @staticmethod
    def _schema_version(key: str) -> int:
        try:
            prefix = key.split(":", 1)[0]
            return int(prefix.removeprefix("q"))
        except Exception:
            return 0

    def get(self, key: str, *, max_age: float) -> Any | None:
        if max_age <= 0:
            self.misses += 1
            return None
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT schema_version, stored_wall_ns, payload FROM semantic_cache WHERE fingerprint=?",
                    (key,),
                ).fetchone()
            if row is None:
                self.misses += 1
                return None
            schema_version, stored_wall_ns, payload = row
            if int(schema_version) != self._schema_version(key):
                self.invalidate(key)
                self.misses += 1
                return None
            age = (time.time_ns() - int(stored_wall_ns)) / 1_000_000_000
            if age > max_age:
                self.invalidate(key)
                self.misses += 1
                return None
            value = _decode_result(str(payload))
            self.hits += 1
            return value
        except Exception as exc:
            self.errors += 1
            self.misses += 1
            _LOG.warning("semantic L2 cache read failed; treating as miss: %s", exc)
            return None

    def put(self, key: str, value: Any) -> None:
        try:
            payload = _encode_result(value)
            if payload is None:
                return
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO semantic_cache(fingerprint, schema_version, stored_wall_ns, payload)
                    VALUES(?,?,?,?)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                      schema_version=excluded.schema_version,
                      stored_wall_ns=excluded.stored_wall_ns,
                      payload=excluded.payload
                    """,
                    (key, self._schema_version(key), time.time_ns(), payload),
                )
                self._conn.commit()
            self.writes += 1
        except Exception as exc:
            self.errors += 1
            _LOG.warning("semantic L2 cache write failed; ignoring optimization failure: %s", exc)

    def invalidate(self, key: str) -> bool:
        try:
            with self._lock:
                cur = self._conn.execute(
                    "DELETE FROM semantic_cache WHERE fingerprint=?", (key,)
                )
                self._conn.commit()
            return bool(cur.rowcount)
        except Exception as exc:
            self.errors += 1
            _LOG.warning("semantic L2 cache invalidate failed: %s", exc)
            return False

    def clear(self) -> None:
        try:
            with self._lock:
                self._conn.execute("DELETE FROM semantic_cache")
                self._conn.commit()
        except Exception as exc:
            self.errors += 1
            _LOG.warning("semantic L2 cache clear failed: %s", exc)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __len__(self) -> int:
        try:
            with self._lock:
                row = self._conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0


class TieredSemanticQueryCache:
    """L1 memory + L2 SQLite with promotion on L2 hit."""

    def __init__(
        self,
        l1: SemanticQueryCache,
        l2: SQLiteSemanticQueryCache,
    ) -> None:
        self.l1 = l1
        self.l2 = l2

    def get(self, key: str, *, max_age: float) -> Any | None:
        value = self.l1.get(key, max_age=max_age)
        if value is not None:
            return value
        value = self.l2.get(key, max_age=max_age)
        if value is not None:
            self.l1.put(key, value)
        return value

    def put(self, key: str, value: Any) -> None:
        self.l1.put(key, value)
        self.l2.put(key, value)

    def invalidate(self, key: str) -> bool:
        return self.l1.invalidate(key) or self.l2.invalidate(key)

    def clear(self) -> None:
        self.l1.clear()
        self.l2.clear()

    def close(self) -> None:
        self.l2.close()

    def __len__(self) -> int:
        return len(self.l1)
