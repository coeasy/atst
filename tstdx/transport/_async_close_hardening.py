# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Cancellation-atomic shutdown for :class:`AsyncConnectionPool`.

The legacy async close path marked the pool closed before awaiting each slot lock.
Cancellation in that preparation phase could therefore leave ``_closed=True``
with only part of the slot set retired and live connections still attached. A
second close then returned immediately and could not repair the leak.

Shutdown now has three explicit phases:

1. acquire every current/retired slot lock while the old pool state is untouched;
2. publish the closed generation atomically with no await between mutations;
3. cancel heartbeat and drain every connection before propagating cancellation
   or any cleanup failure.

Repeated ``close()`` calls still perform cleanup, so an already-closed pool is a
resource state, not a reason to skip idempotent draining.
"""

from __future__ import annotations

import asyncio

from . import async_ as _impl


def _unique_slots(pool: _impl.AsyncConnectionPool) -> list[_impl.AsyncSlot]:
    slots: list[_impl.AsyncSlot] = []
    seen: set[int] = set()
    for slot in [*pool._slots, *pool._retired_slots]:
        identity = id(slot)
        if identity in seen:
            continue
        seen.add(identity)
        slots.append(slot)
    return slots


async def _cleanup_committed_close(
    pool: _impl.AsyncConnectionPool,
    *,
    heartbeat: asyncio.Task | None,
    slots: list[_impl.AsyncSlot],
) -> None:
    """Drain every shutdown resource, then surface the first cleanup failure."""

    first_error: BaseException | None = None
    current = asyncio.current_task()
    if heartbeat is not None and heartbeat is not current:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            first_error = exc

    for slot in slots:
        try:
            await pool._drop(slot)
        except BaseException as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error


async def _await_cleanup_before_cancellation(
    cleanup_task: asyncio.Task,
) -> None:
    """Shield cleanup from caller cancellation and re-raise only after drain."""

    cancelled = False
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            cancelled = True
            continue

    # Surface a cleanup failure rather than disguising it as a successful close.
    # ``result()`` is non-awaiting here because the task is already done.
    cleanup_task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _close(self: _impl.AsyncConnectionPool) -> None:
    slots: list[_impl.AsyncSlot]
    heartbeat: asyncio.Task | None

    async with self._lock:
        slots = _unique_slots(self)
        locked: list[_impl.AsyncSlot] = []
        try:
            # Preparation is cancellation-safe: no pool/slot state changes until
            # every lock that participates in the close transaction is owned.
            for slot in slots:
                await slot.lock.acquire()
                locked.append(slot)

            # Commit phase contains no await. Cancellation can therefore observe
            # either the complete old generation or the complete closed one.
            if not self._closed:
                self._closed = True
                self._generation += 1
            for slot in slots:
                slot.retired = True
            heartbeat = self._hb
            self._hb = None
        finally:
            for slot in reversed(locked):
                slot.lock.release()

    cleanup_task = asyncio.create_task(
        _cleanup_committed_close(
            self,
            heartbeat=heartbeat,
            slots=slots,
        ),
        name="tstdx-pool-close-cleanup",
    )
    await _await_cleanup_before_cancellation(cleanup_task)


_impl.AsyncConnectionPool.close = _close
