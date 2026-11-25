# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""T-1 regression: the sync heartbeat must read the generation under ``slot.lock``.

``update_hosts`` can rebind a reused slot's ``host``/``generation`` while keeping the
same connection.  Reading ``slot.generation`` outside the slot lock (the old code did
it before entering the lock) allowed a heartbeat to ping the old connection and then
attribute the failure to the newly published host, or drop a genuine failure.  The
generation must be captured in the same lock scope as the connection.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from atst.protocol.commands import Family
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool, Slot


class _PingConn:
    connected = True

    def __init__(self, on_ping: Any) -> None:
        self.stats = SimpleNamespace(last_used=0.0, created_at=0.0)
        self._on_ping = on_ping

    def ping(self, _cmd: int) -> float:
        self._on_ping()
        return 1.0

    def close(self) -> None:
        return None


def _lock_is_held(lock: threading.Lock) -> bool:
    """Non-blocking probe: on a non-reentrant Lock, acquiring while held returns False."""
    if lock.acquire(blocking=False):
        lock.release()
        return False
    return True


def test_heartbeat_captures_generation_inside_the_slot_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = ConnectionPool(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD)],
        slots_per_host=1,
        heartbeat_interval=1,
        idle_timeout=0.0,  # disable M6 so the fake connection is not reclaimed before ping
    )
    pinged = threading.Event()
    unlocked_reads: list[int] = []
    armed = True
    values: dict[int, int] = {}

    def _get(self: Slot) -> int:
        if armed and not _lock_is_held(self.lock):
            unlocked_reads.append(threading.get_ident())
        return values.get(id(self), 0)

    def _set(self: Slot, value: int) -> None:
        values[id(self)] = value

    monkeypatch.setattr(Slot, "generation", property(_get, _set))

    def _on_ping() -> None:
        nonlocal armed
        armed = False  # only the initial capture is under test; later reads are bookkeeping
        pinged.set()

    pool._slots[0].conn = _PingConn(_on_ping)
    try:
        pool._start_heartbeat()
        assert pinged.wait(timeout=5.0), "heartbeat never pinged — test setup may be wrong"
        assert unlocked_reads == [], (
            "heartbeat read slot.generation outside slot.lock: it can ping an old "
            "connection and misattribute the failure to the newly published host"
        )
    finally:
        pool.close()
