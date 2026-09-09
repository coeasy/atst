# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Generation-safe host update and background probe provenance.

The v1.0 generation/lease model protects in-flight connections, while the v12
provider work separates selector identity, live request health and background
probe latency. This layer joins those contracts:

* ``update_hosts`` never replaces selector identity/live health with speed-test
  failure state;
* every retained endpoint gets a fresh generation HostEntry, so old references
  cannot share mutable health with the newly published generation;
* idle connections are still reusable, leased slots retire normally;
* a background speed test may update/persist results only if the generation it
  started from is still current when probing finishes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import replace

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import pool as _sync_impl
from .hosts import HostEntry, RankingStore, parse_server

_LOG = logging.getLogger("tstdx.transport")


def _validated_updates(hosts: Sequence[HostEntry], *, family: str) -> list[HostEntry]:
    items: list[HostEntry] = []
    seen: set[str] = set()
    for entry in hosts:
        if not isinstance(entry, HostEntry):
            raise ConfigError(
                f"update_hosts 只接受 HostEntry，收到 {type(entry).__name__}"
            )
        validated = parse_server(entry, family=family)
        if validated.family != family:
            raise ConfigError(
                "update_hosts family 不匹配: "
                f"requested={family!r}, entry={validated.key} family={validated.family!r}"
            )
        if validated.key in seen:
            raise ConfigError(f"update_hosts 存在重复 endpoint: {validated.key}")
        seen.add(validated.key)
        items.append(entry)
    return items


def _new_endpoint(entry: HostEntry, *, family: str) -> HostEntry:
    validated = parse_server(entry, family=family)
    return HostEntry(
        host=validated.host,
        port=validated.port,
        family=validated.family,
        name=validated.name,
        verified=validated.verified,
        connect_ms=validated.connect_ms,
        rtt_ms=validated.rtt_ms,
    )


def _next_generation_host(old: HostEntry, observed: HostEntry) -> HostEntry:
    """Copy old identity/live health and overlay only successful probe latency."""

    fresh = replace(old)
    if observed.connect_ms is not None:
        fresh.connect_ms = observed.connect_ms
    if observed.rtt_ms is not None:
        fresh.rtt_ms = observed.rtt_ms

    # A half-open token belongs to the retiring generation. Carrying the token
    # would strand the new generation with no task able to release it.
    if old.circuit_probe_inflight:
        fresh.circuit = "open"
        fresh.circuit_opened_at = time.time()
    fresh.circuit_probe_inflight = False
    return fresh


def _sync_update_hosts(
    self: _sync_impl.ConnectionPool,
    hosts: Sequence[HostEntry],
) -> list[HostEntry]:
    if not hosts:
        return list(self.hosts)
    observed = _validated_updates(hosts, family=self.family)
    to_close: list[_sync_impl.Slot] = []

    with self._lock:
        self._generation += 1
        generation = self._generation
        # A probe worker belongs to exactly one generation. Moving to a new
        # generation reopens admission for a future worker; any old worker is
        # still harmless because it checks its captured generation before commit.
        self._speedtest_triggered = False
        old_slots = list(self._slots)
        old_by_slot_key = {slot.key: slot for slot in old_slots}
        old_host_by_key = {slot.host.key: slot.host for slot in old_slots}

        published_hosts: list[HostEntry] = []
        for item in observed:
            old_host = old_host_by_key.get(item.key)
            published_hosts.append(
                _next_generation_host(old_host, item)
                if old_host is not None
                else _new_endpoint(item, family=self.family)
            )

        new_slots: list[_sync_impl.Slot] = []
        for host in published_hosts:
            for index in range(self.slots_per_host):
                key = f"{host.key}#{index}"
                old = old_by_slot_key.get(key)
                if old is None:
                    new_slots.append(
                        _sync_impl.Slot(host=host, index=index, generation=generation)
                    )
                    continue
                with old.lock:
                    if old.leases == 0 and not old.retired:
                        old.host = host
                        old.generation = generation
                        new_slots.append(old)
                    else:
                        old.retired = True
                        if old not in self._retired_slots:
                            self._retired_slots.append(old)
                        new_slots.append(
                            _sync_impl.Slot(host=host, index=index, generation=generation)
                        )

        new_keys = {slot.key for slot in new_slots}
        for old in old_slots:
            if old.key in new_keys:
                continue
            with old.lock:
                old.retired = True
                if old not in self._retired_slots:
                    self._retired_slots.append(old)
                if old.leases == 0:
                    to_close.append(old)

        self.hosts = published_hosts
        self._slots = new_slots
        self._rr = 0
        _LOG.info(
            "bestip 热更新主站池：%d 台（复用 %d 槽，generation=%d）",
            len(published_hosts),
            len([slot for slot in new_slots if slot.conn is not None]),
            generation,
        )

    for slot in to_close:
        self._drop(slot)
    return published_hosts


async def _async_update_hosts(
    self: _async_impl.AsyncConnectionPool,
    hosts: Sequence[HostEntry],
) -> list[HostEntry]:
    if not hosts:
        return list(self.hosts)
    observed = _validated_updates(hosts, family=self.family)
    to_close: list[_async_impl.AsyncSlot] = []

    async with self._lock:
        self._generation += 1
        generation = self._generation
        old_slots = list(self._slots)
        old_by_slot_key = {slot.key: slot for slot in old_slots}
        old_host_by_key = {slot.host.key: slot.host for slot in old_slots}

        published_hosts: list[HostEntry] = []
        for item in observed:
            old_host = old_host_by_key.get(item.key)
            published_hosts.append(
                _next_generation_host(old_host, item)
                if old_host is not None
                else _new_endpoint(item, family=self.family)
            )

        new_slots: list[_async_impl.AsyncSlot] = []
        for host in published_hosts:
            for index in range(self.slots_per_host):
                key = f"{host.key}#{index}"
                old = old_by_slot_key.get(key)
                if old is None:
                    new_slots.append(
                        _async_impl.AsyncSlot(host=host, index=index, generation=generation)
                    )
                    continue
                async with old.lock:
                    if old.leases == 0 and not old.retired:
                        old.host = host
                        old.generation = generation
                        new_slots.append(old)
                    else:
                        old.retired = True
                        if old not in self._retired_slots:
                            self._retired_slots.append(old)
                        new_slots.append(
                            _async_impl.AsyncSlot(
                                host=host,
                                index=index,
                                generation=generation,
                            )
                        )

        new_keys = {slot.key for slot in new_slots}
        for old in old_slots:
            if old.key in new_keys:
                continue
            async with old.lock:
                old.retired = True
                if old not in self._retired_slots:
                    self._retired_slots.append(old)
                if old.leases == 0:
                    to_close.append(old)

        self.hosts = published_hosts
        self._slots = new_slots
        self._rr = 0

    for slot in to_close:
        await self._drop(slot)
    return published_hosts


def _sync_trigger_background_speedtest(self: _sync_impl.ConnectionPool) -> None:
    """Probe one snapshot, but commit observations only to the same generation."""

    if self._speedtest_triggered:
        return
    with self._lock:
        if self._speedtest_triggered:
            return
        self._speedtest_triggered = True
        generation = self._generation
        snapshot = tuple(self.hosts)
        family = self.family
        timeout = min(self.timeout, 2.0)

    def _run() -> None:
        try:
            from .speedtest import _apply_probe_observations, rank_hosts, speedtest

            results = speedtest(
                snapshot,
                family=family,
                timeout=timeout,
            )
            with self._lock:
                if self._closed or self._generation != generation:
                    _LOG.info(
                        "丢弃旧 generation 后台测速：started=%d current=%d",
                        generation,
                        self._generation,
                    )
                    return
                _apply_probe_observations(snapshot, results, family=family)
                if family == Family.STANDARD:
                    RankingStore().update(rank_hosts(results))
        except Exception as exc:  # pragma: no cover - optimization must fail open
            _LOG.warning("后台测速失败: %s", exc)

    threading.Thread(
        target=_run,
        name="tstdx-speedtest",
        daemon=True,
    ).start()


setattr(_sync_impl.ConnectionPool, "update_hosts", _sync_update_hosts)
setattr(_sync_impl.ConnectionPool, "_trigger_background_speedtest", _sync_trigger_background_speedtest)
setattr(_async_impl.AsyncConnectionPool, "update_hosts", _async_update_hosts)
