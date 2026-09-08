from __future__ import annotations

from tstdx.errors import InternalError, SourceUnavailable
from tstdx.health import SourceHealthRegistry


def _record(registry: SourceHealthRegistry, exc: BaseException) -> None:
    registry.before_request("tencent", "quote", "quotes")
    registry.record_failure(
        "tencent",
        "quote",
        "quotes",
        exc,
        penalize=registry.should_penalize(exc),
    )


def test_native_programming_errors_do_not_open_provider_circuit() -> None:
    registry = SourceHealthRegistry(failure_threshold=2, cooldown_seconds=60.0)

    _record(registry, ValueError("bad adapter shape"))
    _record(registry, TypeError("bad internal call"))

    state = registry.snapshot("tencent", "quote", "quotes")
    assert state.failures == 2
    assert state.consecutive_failures == 0
    assert state.generation == 0
    assert state.circuit_open is False
    registry.before_request("tencent", "quote", "quotes")


def test_internal_error_does_not_open_provider_circuit() -> None:
    registry = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60.0)
    error = InternalError("internal")

    _record(registry, error)

    state = registry.snapshot("tencent", "quote", "quotes")
    assert state.failures == 1
    assert state.last_error_code == "E9000"
    assert state.consecutive_failures == 0
    assert state.generation == 0
    assert state.circuit_open is False


def test_provider_unavailable_still_opens_provider_circuit() -> None:
    registry = SourceHealthRegistry(failure_threshold=2, cooldown_seconds=60.0)

    _record(registry, SourceUnavailable("upstream failed"))
    _record(registry, SourceUnavailable("upstream failed again"))

    state = registry.snapshot("tencent", "quote", "quotes")
    assert state.failures == 2
    assert state.consecutive_failures == 2
    assert state.generation == 2
    assert state.circuit_open is True
