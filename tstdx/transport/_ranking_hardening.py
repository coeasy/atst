# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Harden STANDARD V1 ranking persistence to probe-only provenance.

Historical V1 files may contain process-local health/circuit fields because older
writers serialized most of ``HostEntry``. Those files remain readable, but the
runtime fields are discarded on load and are never written again. Persistent
ranking may influence only successful probe latency ordering; it may not
resurrect a prior process' failures, circuit state, verification identity, or
live health.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import hosts as _impl

_PERSISTED_FIELDS = frozenset({"host", "port", "family", "connect_ms", "rtt_ms"})
_REQUIRED_IDENTITY_FIELDS = frozenset({"host", "port", "family"})
_LEGACY_RUNTIME_FIELDS = frozenset(
    {
        "name",
        "verified",
        "failures",
        "biz_failures",
        "last_ok",
        "last_error",
        "circuit",
        "consec_weighted",
        "circuit_opened_at",
    }
)
_LEGACY_ALLOWED_FIELDS = _PERSISTED_FIELDS | _LEGACY_RUNTIME_FIELDS | _impl._EPHEMERAL_FIELDS


def _probe_only_entry(entry: _impl.HostEntry) -> _impl.HostEntry:
    validated = _impl._validated_entry(entry, source="RankingStore")
    return _impl.HostEntry(
        host=validated.host,
        port=validated.port,
        family=validated.family,
        connect_ms=validated.connect_ms,
        rtt_ms=validated.rtt_ms,
    )


def _probe_payload(entry: _impl.HostEntry) -> dict[str, Any]:
    probe = _probe_only_entry(entry)
    return {
        "host": probe.host,
        "port": probe.port,
        "family": probe.family,
        "connect_ms": probe.connect_ms,
        "rtt_ms": probe.rtt_ms,
    }


def _entry_from_legacy_payload(item: Mapping[str, Any]) -> _impl.HostEntry | None:
    keys = set(item)
    if _impl._EPHEMERAL_FIELDS.intersection(keys):
        # live_rtt/live_ok/probe tokens were never legitimate disk provenance.
        return None
    if not _REQUIRED_IDENTITY_FIELDS.issubset(keys):
        return None
    if keys - _LEGACY_ALLOWED_FIELDS:
        return None

    try:
        entry = _impl.HostEntry(
            host=item["host"],
            port=item["port"],
            family=item["family"],
            connect_ms=item.get("connect_ms"),
            rtt_ms=item.get("rtt_ms"),
        )
        entry = _impl._validated_entry(entry, source="ranking entry")
    except (KeyError, TypeError, ValueError, ConfigError):
        return None
    if entry.family != Family.STANDARD or entry.rtt_ms is None:
        return None
    return entry


def _load(self: _impl.RankingStore) -> dict[str, _impl.HostEntry]:
    if not self.path.exists():
        return {}
    try:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, Mapping):
        return {}
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != self.VERSION:
        return {}
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, Mapping):
        return {}

    out: dict[str, _impl.HostEntry] = {}
    seen_embedded: set[str] = set()
    for persisted_key, item in raw_entries.items():
        if not isinstance(persisted_key, str) or not isinstance(item, Mapping):
            continue
        entry = _entry_from_legacy_payload(item)
        if entry is None:
            continue
        if persisted_key != entry.key or entry.key in seen_embedded:
            return {}
        seen_embedded.add(entry.key)
        out[entry.key] = entry
    return out


def _save(self: _impl.RankingStore, entries: Iterable[_impl.HostEntry]) -> None:
    items = [entry for entry in self._require_standard(entries) if entry.rtt_ms is not None]
    data = {
        "version": self.VERSION,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entries": {entry.key: _probe_payload(entry) for entry in items},
    }
    self.path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=f"{self.path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
            temp_path = Path(stream.name)
        temp_path.replace(self.path)
    finally:
        if temp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                temp_path.unlink()


def _merge(
    self: _impl.RankingStore,
    entries: Iterable[_impl.HostEntry],
) -> dict[str, _impl.HostEntry]:
    """Merge only successful probe latency; process health never survives restart."""

    items = self._require_standard(entries)
    known = self.load()
    for entry in items:
        if entry.rtt_ms is None:
            # A failed probe invalidates stale positive latency when callers keep
            # failures. The failure itself is not persisted as live/circuit state.
            if entry.failures > 0 or bool(entry.last_error):
                known.pop(entry.key, None)
            continue

        previous = known.get(entry.key)
        if previous is None:
            known[entry.key] = _probe_only_entry(entry)
            continue
        previous.rtt_ms = entry.rtt_ms
        if entry.connect_ms is not None:
            previous.connect_ms = entry.connect_ms
    return known


def _apply_ranked_observation(
    base: _impl.HostEntry,
    ranked: _impl.HostEntry,
) -> _impl.HostEntry:
    """Overlay only persistent probe latency onto selector-owned runtime state."""

    if ranked.family != base.family or ranked.key != base.key:
        raise ConfigError(
            "ranking observation identity mismatch: "
            f"base={base.key}/{base.family!r}, ranked={ranked.key}/{ranked.family!r}"
        )
    return replace(base, connect_ms=ranked.connect_ms, rtt_ms=ranked.rtt_ms)


_impl.RankingStore.load = _load
_impl.RankingStore.save = _save
_impl.RankingStore.merge = _merge
_impl._apply_ranked_observation = _apply_ranked_observation
