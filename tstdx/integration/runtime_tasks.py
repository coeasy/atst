# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Bounded background task storage for canonical runtime surfaces."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..error_envelope import to_error_envelope

__all__ = ["RuntimeTaskStore", "RuntimeTaskStoreFull"]

_MAX_RESULT_ROWS = 1000
_MAX_RESULT_BYTES = 1024 * 1024


class RuntimeTaskStoreFull(RuntimeError):
    pass


def _clamp_result(result: Any) -> Any:
    if isinstance(result, list) and len(result) > _MAX_RESULT_ROWS:
        result = {
            "truncated": True,
            "total_rows": len(result),
            "rows": result[:_MAX_RESULT_ROWS],
        }
    try:
        size = len(json.dumps(result, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"truncated": True, "note": "unserializable result dropped"}
    if size > _MAX_RESULT_BYTES:
        return {"truncated": True, "note": f"result exceeds {_MAX_RESULT_BYTES} bytes"}
    return result


class RuntimeTaskStore:
    """Threaded task store retaining only bounded results and safe envelopes."""

    def __init__(self, *, max_tasks: int = 200, retention_seconds: float = 3600.0) -> None:
        if max_tasks <= 0:
            raise ValueError("max_tasks must be positive")
        if retention_seconds <= 0:
            raise ValueError("retention_seconds must be positive")
        self.max_tasks = int(max_tasks)
        self.retention_seconds = float(retention_seconds)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _expire_locked(self, now: float) -> None:
        for record in self._tasks.values():
            completed = record.get("completed_at")
            if completed is None or now - completed < self.retention_seconds:
                continue
            record["result"] = None
            record["error"] = None
            record["expired"] = True

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        with self._lock:
            now = time.time()
            self._expire_locked(now)
            active = sum(
                1 for record in self._tasks.values() if record["status"] in {"pending", "running"}
            )
            if active >= self.max_tasks:
                raise RuntimeTaskStoreFull(
                    f"too many active tasks ({active}); max_tasks={self.max_tasks}"
                )
            if len(self._tasks) >= self.max_tasks:
                finished = sorted(
                    (
                        (task_id, record)
                        for task_id, record in self._tasks.items()
                        if record["status"] in {"done", "failed", "cancelled"}
                    ),
                    key=lambda item: item[1].get("completed_at") or item[1]["submitted_at"],
                )
                if finished:
                    self._tasks.pop(finished[0][0], None)
            task_id = uuid.uuid4().hex[:12]
            record = {
                "task_id": task_id,
                "status": "pending",
                "submitted_at": now,
                "completed_at": None,
                "result": None,
                "error": None,
                "expired": False,
            }
            self._tasks[task_id] = record

        def _run() -> None:
            with self._lock:
                if record["status"] == "cancelled":
                    return
                record["status"] = "running"
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                envelope = to_error_envelope(exc).to_dict()
                with self._lock:
                    if record["status"] == "cancelled":
                        return
                    record["status"] = "failed"
                    record["error"] = envelope
                    record["completed_at"] = time.time()
            else:
                with self._lock:
                    if record["status"] == "cancelled":
                        return
                    record["status"] = "done"
                    record["result"] = _clamp_result(result)
                    record["completed_at"] = time.time()

        threading.Thread(target=_run, name=f"runtime-task-{task_id}", daemon=True).start()
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._expire_locked(time.time())
            record = self._tasks.get(task_id)
            return dict(record) if record is not None else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is None or record["status"] in {"done", "failed", "cancelled"}:
                return False
            record["status"] = "cancelled"
            record["completed_at"] = time.time()
            record["result"] = None
            record["error"] = None
            return True

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            self._expire_locked(time.time())
            return [
                {key: value for key, value in record.items() if key not in {"result", "error"}}
                for record in self._tasks.values()
            ]
