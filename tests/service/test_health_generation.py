from __future__ import annotations

import threading

from tstdx.errors import SourceUnavailable
from tstdx.health import SourceHealthRegistry


def test_stale_success_cannot_reset_newer_concurrent_failure() -> None:
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    both_started = threading.Barrier(2)
    failure_recorded = threading.Event()
    errors: list[BaseException] = []

    def slow_success() -> None:
        try:
            health.before_request("tencent", "quote", "quotes")
            both_started.wait(timeout=1.0)
            failure_recorded.wait(timeout=1.0)
            health.record_success("tencent", "quote", "quotes")
        except BaseException as exc:
            errors.append(exc)

    def fast_failure() -> None:
        try:
            health.before_request("tencent", "quote", "quotes")
            both_started.wait(timeout=1.0)
            health.record_failure(
                "tencent",
                "quote",
                "quotes",
                SourceUnavailable("provider down"),
            )
            failure_recorded.set()
        except BaseException as exc:
            errors.append(exc)

    success_thread = threading.Thread(target=slow_success)
    failure_thread = threading.Thread(target=fast_failure)
    success_thread.start()
    failure_thread.start()
    success_thread.join(timeout=2.0)
    failure_thread.join(timeout=2.0)

    assert errors == []
    state = health.snapshot("tencent", "quote", "quotes")
    assert state.successes == 1
    assert state.failures == 1
    assert state.generation == 1
    assert state.consecutive_failures == 1
    assert state.last_error_code == "E7050"
    assert state.circuit_open is True


def test_new_generation_success_still_recovers_provider() -> None:
    health = SourceHealthRegistry(failure_threshold=2, cooldown_seconds=60)
    health.before_request("tencent", "quote", "quotes")
    health.record_failure("tencent", "quote", "quotes", SourceUnavailable("down"))

    health.before_request("tencent", "quote", "quotes")
    health.record_success("tencent", "quote", "quotes")

    state = health.snapshot("tencent", "quote", "quotes")
    assert state.generation == 1
    assert state.consecutive_failures == 0
    assert state.circuit_open is False
