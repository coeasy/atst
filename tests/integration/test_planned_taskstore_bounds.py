from __future__ import annotations

import time

from tstdx.integration.http_app import PlannedTaskStore


def _wait_result(store: PlannedTaskStore, task_id: str) -> dict:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        payload = store.get(task_id)
        assert payload is not None
        if payload["status"] in {"done", "failed", "cancelled"}:
            return payload
        time.sleep(0.005)
    raise AssertionError(f"task {task_id} did not finish")


def test_planned_taskstore_clamps_large_list_before_storage() -> None:
    store = PlannedTaskStore(max_tasks=1, max_workers=1)
    try:
        task_id = store.submit(lambda: list(range(1001)))
        payload = _wait_result(store, task_id)
        assert payload["status"] == "done"
        result = payload["result"]
        assert result["truncated"] is True
        assert result["total_rows"] == 1001
        assert len(result["rows"]) == 1000
        # The bounded value is what the manager actually retained, not merely a
        # response-time projection of an unbounded in-memory result.
        retained = store._manager.get(task_id).result
        assert retained == result
    finally:
        store.close()


def test_planned_taskstore_clamps_oversized_serialized_result_before_storage() -> None:
    store = PlannedTaskStore(max_tasks=1, max_workers=1)
    try:
        task_id = store.submit(lambda: {"blob": "x" * (1024 * 1024 + 128)})
        payload = _wait_result(store, task_id)
        assert payload["status"] == "done"
        assert payload["result"] == {
            "truncated": True,
            "note": "result exceeds 1048576 bytes",
        }
        assert store._manager.get(task_id).result == payload["result"]
    finally:
        store.close()
