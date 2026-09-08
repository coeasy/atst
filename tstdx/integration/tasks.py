# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Bounded background task lifecycle for integration adapters.

The historical HTTP ``TaskStore`` started one daemon thread per task and marked a
running task ``cancelled`` immediately.  That removed it from the active count
while the thread kept running, allowing ``max_tasks`` bypass and orphan work.

``TaskManager`` makes task state reflect actual execution:

``pending -> running -> done/failed``
``pending/running -> cancel_requested -> cancelled``

A ``cancel_requested`` task remains active until the Future is cancelled before
start or the worker actually exits. Running/cancel-requested records cannot be
deleted.  ``max_tasks`` bounds running + queued work, so ThreadPoolExecutor's
internal unbounded queue is never exposed unboundedly through this API.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, TypeVar

from ..errors import RateLimitedLocal, ValidationError

__all__ = [
    "CancellationToken",
    "TaskStatus",
    "TaskSnapshot",
    "TaskManager",
]

T = TypeVar("T")


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


_ACTIVE = {
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.CANCEL_REQUESTED,
}
_FINISHED = {
    TaskStatus.CANCELLED,
    TaskStatus.DONE,
    TaskStatus.FAILED,
    TaskStatus.EXPIRED,
}


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled()


class TaskCancelled(Exception):
    """Internal cooperative cancellation signal."""


@dataclass(frozen=True, slots=True)
class TaskSnapshot(Generic[T]):
    id: str
    status: TaskStatus
    created_at: float
    started_at: float | None
    finished_at: float | None
    cancel_requested_at: float | None
    result: T | None
    error: str | None

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED


@dataclass(slots=True)
class _TaskRecord(Generic[T]):
    id: str
    status: TaskStatus
    created_at: float
    token: CancellationToken
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested_at: float | None = None
    result: T | None = None
    error: str | None = None
    future: Future[Any] | None = field(default=None, repr=False)

    def snapshot(self) -> TaskSnapshot[T]:
        return TaskSnapshot(
            id=self.id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            cancel_requested_at=self.cancel_requested_at,
            result=self.result,
            error=self.error,
        )


class TaskManager:
    """Bounded Future-based task manager.

    Parameters
    ----------
    max_workers:
        Maximum simultaneously executing worker threads.
    max_tasks:
        Maximum active tasks (pending + running + cancel_requested). This is also
        the effective queue bound.
    retention_seconds:
        Finished records older than this can be marked expired/purged.
    """

    def __init__(
        self,
        *,
        max_workers: int = 4,
        max_tasks: int = 128,
        retention_seconds: float = 3600.0,
        thread_name_prefix: str = "tstdx-task",
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be > 0")
        if max_tasks < max_workers:
            raise ValueError("max_tasks must be >= max_workers")
        if retention_seconds < 0:
            raise ValueError("retention_seconds must be >= 0")
        self.max_workers = int(max_workers)
        self.max_tasks = int(max_tasks)
        self.retention_seconds = float(retention_seconds)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = threading.RLock()
        self._records: dict[str, _TaskRecord[Any]] = {}
        self._accepting = True
        self._closed = False

    def _active_count_locked(self) -> int:
        return sum(record.status in _ACTIVE for record in self._records.values())

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count_locked()

    def _ensure_accepting_locked(self) -> None:
        if not self._accepting or self._closed:
            raise RuntimeError("TaskManager is draining/closed")

    def submit(self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> str:
        """Submit a normal callable without injecting a cancellation token."""
        return self._submit(False, fn, args, kwargs)

    def submit_cancellable(
        self,
        fn: Callable[..., T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Submit ``fn(token, *args, **kwargs)`` for cooperative cancellation."""
        return self._submit(True, fn, args, kwargs)

    def _submit(
        self,
        cancellation_aware: bool,
        fn: Callable[..., T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        task_id = uuid.uuid4().hex
        record: _TaskRecord[T] = _TaskRecord(
            id=task_id,
            status=TaskStatus.PENDING,
            created_at=time.time(),
            token=CancellationToken(),
        )
        with self._lock:
            self._ensure_accepting_locked()
            active = self._active_count_locked()
            if active >= self.max_tasks:
                raise RateLimitedLocal(
                    "后台任务队列已满",
                    context={
                        "active_tasks": active,
                        "max_tasks": self.max_tasks,
                    },
                )
            self._records[task_id] = record
            try:
                future = self._executor.submit(
                    self._run,
                    record,
                    cancellation_aware,
                    fn,
                    args,
                    kwargs,
                )
            except BaseException:
                self._records.pop(task_id, None)
                raise
            record.future = future
        return task_id

    def _run(
        self,
        record: _TaskRecord[T],
        cancellation_aware: bool,
        fn: Callable[..., T],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        with self._lock:
            if record.token.cancelled:
                record.status = TaskStatus.CANCELLED
                record.finished_at = time.time()
                return
            record.status = TaskStatus.RUNNING
            record.started_at = time.time()

        try:
            if cancellation_aware:
                result = fn(record.token, *args, **kwargs)
            else:
                result = fn(*args, **kwargs)
        except TaskCancelled:
            with self._lock:
                record.status = TaskStatus.CANCELLED
                record.finished_at = time.time()
                record.result = None
                record.error = None
        except BaseException as exc:
            with self._lock:
                if record.token.cancelled:
                    record.status = TaskStatus.CANCELLED
                    record.error = f"{type(exc).__name__}: {exc}"
                else:
                    record.status = TaskStatus.FAILED
                    record.error = f"{type(exc).__name__}: {exc}"
                record.finished_at = time.time()
        else:
            with self._lock:
                if record.token.cancelled:
                    record.status = TaskStatus.CANCELLED
                    record.result = None
                else:
                    record.status = TaskStatus.DONE
                    record.result = result
                record.finished_at = time.time()

    def get(self, task_id: str) -> TaskSnapshot[Any]:
        with self._lock:
            try:
                return self._records[task_id].snapshot()
            except KeyError as exc:
                raise KeyError(task_id) from exc

    def list(self) -> list[TaskSnapshot[Any]]:
        with self._lock:
            return [record.snapshot() for record in self._records.values()]

    def cancel(self, task_id: str) -> TaskSnapshot[Any]:
        """Request cancellation without lying about worker completion."""
        with self._lock:
            try:
                record = self._records[task_id]
            except KeyError as exc:
                raise KeyError(task_id) from exc
            if record.status in _FINISHED:
                return record.snapshot()
            if record.status is not TaskStatus.CANCEL_REQUESTED:
                record.status = TaskStatus.CANCEL_REQUESTED
                record.cancel_requested_at = time.time()
                record.token.cancel()
            future = record.future
            if future is not None and future.cancel():
                # Pending Future will never enter _run, so cancellation is now
                # terminal and may release its active slot immediately.
                record.status = TaskStatus.CANCELLED
                record.finished_at = time.time()
            return record.snapshot()

    def delete(self, task_id: str) -> None:
        """Delete only finished records; never orphan running work."""
        with self._lock:
            try:
                record = self._records[task_id]
            except KeyError as exc:
                raise KeyError(task_id) from exc
            if record.status in _ACTIVE:
                raise ValidationError(
                    "运行中或等待取消的任务不能删除",
                    context={"task_id": task_id, "status": record.status.value},
                )
            self._records.pop(task_id, None)

    def expire_completed(self, *, now: float | None = None) -> int:
        """Mark completed records older than retention as ``expired``."""
        current = time.time() if now is None else float(now)
        changed = 0
        with self._lock:
            for record in self._records.values():
                if record.status not in {
                    TaskStatus.DONE,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                }:
                    continue
                finished = record.finished_at
                if finished is None:
                    continue
                if current - finished >= self.retention_seconds:
                    record.status = TaskStatus.EXPIRED
                    record.result = None
                    changed += 1
        return changed

    def purge_expired(self) -> int:
        with self._lock:
            ids = [
                task_id
                for task_id, record in self._records.items()
                if record.status is TaskStatus.EXPIRED
            ]
            for task_id in ids:
                self._records.pop(task_id, None)
            return len(ids)

    def shutdown(self, *, wait: bool = True) -> None:
        """Drain existing work and reject new submissions."""
        with self._lock:
            if self._closed:
                return
            self._accepting = False
        self._executor.shutdown(wait=wait, cancel_futures=False)
        with self._lock:
            self._closed = True

    close = shutdown

    def __enter__(self) -> TaskManager:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown(wait=True)
