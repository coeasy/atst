# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Explicit fail-closed lifecycle contract for streaming runtimes."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

from ..errors import SubscriptionError

__all__ = ["StreamState", "StreamLifecycle", "StreamLifecycleSnapshot"]


class StreamState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StreamLifecycleSnapshot:
    state: StreamState
    failure_reason: str | None
    generation: int


class StreamLifecycle:
    """Thread-safe one-shot stream state machine.

    ``CLOSED`` and ``FAILED`` are terminal.  Cleanup is still allowed after a
    failure, but cleanup must never erase the failure state or reason.  This is
    important for observability and, more critically, prevents a failed worker
    from looking like a cleanly closed/restartable stream.
    """

    def __init__(self) -> None:
        self._state = StreamState.CREATED
        self._failure_reason: str | None = None
        self._generation = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> StreamState:
        with self._lock:
            return self._state

    @property
    def failure_reason(self) -> str | None:
        with self._lock:
            return self._failure_reason

    def snapshot(self) -> StreamLifecycleSnapshot:
        with self._lock:
            return StreamLifecycleSnapshot(
                state=self._state,
                failure_reason=self._failure_reason,
                generation=self._generation,
            )

    def require_subscribable(self) -> None:
        with self._lock:
            if self._state not in {StreamState.CREATED, StreamState.RUNNING}:
                raise SubscriptionError(
                    "stream 已进入终止状态，不能新增订阅",
                    context={"state": self._state.value, "phase": "stream_lifecycle"},
                )

    def begin_start(self) -> bool:
        """Transition CREATED -> RUNNING.

        Returns ``False`` when already RUNNING so a healthy repeated ``start``
        may remain idempotent. All terminal/stop states fail closed.
        """
        with self._lock:
            if self._state is StreamState.RUNNING:
                return False
            if self._state is not StreamState.CREATED:
                raise SubscriptionError(
                    "stream 已进入终止状态，不能重新启动",
                    context={"state": self._state.value, "phase": "stream_lifecycle"},
                )
            self._state = StreamState.RUNNING
            self._generation += 1
            return True

    def begin_stop(self) -> bool:
        """Begin cleanup without erasing a terminal failure.

        ``FAILED`` returns ``True`` so callers may still release sockets/tasks,
        but the lifecycle stays FAILED. ``CLOSED`` is already fully cleaned.
        """
        with self._lock:
            if self._state is StreamState.CLOSED:
                return False
            if self._state is StreamState.FAILED:
                return True
            if self._state is not StreamState.STOPPING:
                self._state = StreamState.STOPPING
            return True

    def close(self) -> None:
        """Mark a successful cleanup closed, preserving FAILED forever."""
        with self._lock:
            if self._state is not StreamState.FAILED:
                self._state = StreamState.CLOSED

    def fail(self, reason: str) -> None:
        with self._lock:
            if self._state in {StreamState.CLOSED, StreamState.STOPPING, StreamState.FAILED}:
                return
            self._failure_reason = str(reason) or "stream worker failed"
            self._state = StreamState.FAILED

    def assert_running_worker(self, *, worker_alive: bool) -> None:
        """Fail closed if RUNNING is inconsistent with worker reality."""
        with self._lock:
            if self._state is StreamState.RUNNING and not worker_alive:
                self._failure_reason = "stream state is RUNNING but worker is not alive"
                self._state = StreamState.FAILED
                raise SubscriptionError(
                    "stream worker 状态不一致，实例已 fail-closed",
                    context={
                        "state": self._state.value,
                        "phase": "stream_lifecycle",
                        "worker_alive": False,
                    },
                )
