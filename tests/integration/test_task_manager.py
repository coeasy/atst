from __future__ import annotations

import threading
import time

import pytest

from tstdx.errors import RateLimitedLocal, ValidationError
from tstdx.integration.tasks import TaskManager, TaskStatus


def _wait_status(
    manager: TaskManager,
    task_id: str,
    expected: set[TaskStatus],
    *,
    timeout: float = 2.0,
) -> TaskStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.get(task_id).status
        if status in expected:
            return status
        time.sleep(0.005)
    raise AssertionError(
        f"task {task_id} did not reach {sorted(x.value for x in expected)}; "
        f"current={manager.get(task_id).status.value}"
    )


def test_max_tasks_bounds_running_plus_pending_work() -> None:
    gate = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=2)
    try:
        first = manager.submit(lambda: gate.wait(1.0))
        _wait_status(manager, first, {TaskStatus.RUNNING})
        second = manager.submit(lambda: 2)
        assert manager.get(second).status is TaskStatus.PENDING
        assert manager.active_count == 2

        with pytest.raises(RateLimitedLocal):
            manager.submit(lambda: 3)
    finally:
        gate.set()
        manager.shutdown(wait=True)


def test_running_cancel_request_remains_active_until_worker_exits() -> None:
    gate = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=1)
    try:
        task_id = manager.submit(lambda: gate.wait(1.0))
        _wait_status(manager, task_id, {TaskStatus.RUNNING})

        snapshot = manager.cancel(task_id)
        assert snapshot.status is TaskStatus.CANCEL_REQUESTED
        assert manager.active_count == 1
        with pytest.raises(RateLimitedLocal):
            manager.submit(lambda: None)

        gate.set()
        _wait_status(manager, task_id, {TaskStatus.CANCELLED})
        assert manager.active_count == 0
        replacement = manager.submit(lambda: "ok")
        _wait_status(manager, replacement, {TaskStatus.DONE})
    finally:
        gate.set()
        manager.shutdown(wait=True)


def test_pending_future_cancel_releases_slot_without_worker_execution() -> None:
    gate = threading.Event()
    executed = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=2)
    try:
        running = manager.submit(lambda: gate.wait(1.0))
        _wait_status(manager, running, {TaskStatus.RUNNING})
        pending = manager.submit(lambda: executed.set())
        assert manager.get(pending).status is TaskStatus.PENDING

        snapshot = manager.cancel(pending)
        assert snapshot.status is TaskStatus.CANCELLED
        assert manager.active_count == 1
        assert not executed.is_set()
    finally:
        gate.set()
        manager.shutdown(wait=True)
    assert not executed.is_set()


def test_delete_rejects_running_and_cancel_requested_tasks() -> None:
    gate = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=1)
    try:
        task_id = manager.submit(lambda: gate.wait(1.0))
        _wait_status(manager, task_id, {TaskStatus.RUNNING})
        with pytest.raises(ValidationError):
            manager.delete(task_id)

        manager.cancel(task_id)
        with pytest.raises(ValidationError):
            manager.delete(task_id)

        gate.set()
        _wait_status(manager, task_id, {TaskStatus.CANCELLED})
        manager.delete(task_id)
        with pytest.raises(KeyError):
            manager.get(task_id)
    finally:
        gate.set()
        manager.shutdown(wait=True)


def test_cooperative_task_observes_cancellation_token() -> None:
    started = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=1)

    def work(token) -> str:  # noqa: ANN001
        started.set()
        while not token.cancelled:
            token.wait(0.005)
        token.raise_if_cancelled()
        return "unreachable"

    try:
        task_id = manager.submit_cancellable(work)
        assert started.wait(1.0)
        manager.cancel(task_id)
        _wait_status(manager, task_id, {TaskStatus.CANCELLED})
        assert manager.get(task_id).result is None
    finally:
        manager.shutdown(wait=True)


def test_finished_records_expire_and_purge() -> None:
    manager = TaskManager(max_workers=1, max_tasks=1, retention_seconds=0.0)
    try:
        task_id = manager.submit(lambda: 42)
        _wait_status(manager, task_id, {TaskStatus.DONE})
        assert manager.expire_completed() == 1
        assert manager.get(task_id).status is TaskStatus.EXPIRED
        assert manager.get(task_id).result is None
        assert manager.purge_expired() == 1
        with pytest.raises(KeyError):
            manager.get(task_id)
    finally:
        manager.shutdown(wait=True)


def test_shutdown_rejects_new_work() -> None:
    manager = TaskManager(max_workers=1, max_tasks=1)
    manager.shutdown(wait=True)
    with pytest.raises(RuntimeError, match="draining/closed"):
        manager.submit(lambda: None)


def test_nonblocking_shutdown_can_be_followed_by_real_drain() -> None:
    gate = threading.Event()
    started = threading.Event()
    drained = threading.Event()
    manager = TaskManager(max_workers=1, max_tasks=1)

    def work() -> str:
        started.set()
        gate.wait(2.0)
        return "done"

    task_id = manager.submit(work)
    assert started.wait(1.0)
    manager.shutdown(wait=False)

    assert manager.draining is True
    assert manager.get(task_id).status is TaskStatus.RUNNING
    with pytest.raises(RuntimeError, match="draining/closed"):
        manager.submit(lambda: None)

    def finish_shutdown() -> None:
        manager.shutdown(wait=True)
        drained.set()

    waiter = threading.Thread(target=finish_shutdown)
    waiter.start()
    time.sleep(0.05)
    assert drained.is_set() is False
    assert manager.draining is True

    gate.set()
    assert drained.wait(1.0)
    waiter.join(1.0)

    assert manager.draining is False
    assert manager.get(task_id).status is TaskStatus.DONE
    manager.shutdown(wait=True)
