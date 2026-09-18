from __future__ import annotations

import time

from tstdx.error_envelope import to_error_envelope
from tstdx.errors import (
    CommandOffline,
    ConnectionFailed,
    IntegrityViolation,
    SourceUnavailable,
    ValidationError,
)
from tstdx.failure import FailurePolicy
from tstdx.query import ExecutionBudget


def test_connection_failure_allows_only_same_provider_host_recovery() -> None:
    disposition = FailurePolicy().decide(ConnectionFailed("down"))
    assert disposition.retry_same_provider is True
    assert disposition.switch_host is True
    assert disposition.terminal is False
    assert disposition.provider_switch_allowed is False


def test_command_offline_and_integrity_violation_are_terminal() -> None:
    policy = FailurePolicy()
    for exc in (CommandOffline("offline"), IntegrityViolation("bad"), ValidationError("bad arg")):
        disposition = policy.decide(exc)
        assert disposition.retry_same_provider is False
        assert disposition.switch_host is False
        assert disposition.terminal is True
        assert disposition.provider_switch_allowed is False


def test_selected_provider_unavailable_never_authorizes_provider_switch() -> None:
    disposition = FailurePolicy().decide(SourceUnavailable("down"))
    assert disposition.terminal is True
    assert disposition.provider_switch_allowed is False
    assert disposition.reason == "selected_provider_unavailable"


def test_exhausted_query_budget_overrides_transport_retry_advice() -> None:
    budget = ExecutionBudget(deadline_ns=time.monotonic_ns() - 1)
    disposition = FailurePolicy().decide(ConnectionFailed("late"), budget=budget)
    assert disposition.terminal is True
    assert disposition.retry_same_provider is False
    assert disposition.switch_host is False
    assert disposition.reason == "query_deadline_exhausted"


def test_error_envelope_prefers_disposition_retryability() -> None:
    exc = ConnectionFailed(
        "late",
        context={
            "retry_same_provider": False,
            "switch_host": False,
            "terminal": True,
            "provider_switch_allowed": False,
        },
    )
    envelope = to_error_envelope(exc)
    assert envelope.retryable is False
    assert envelope.context["provider_switch_allowed"] is False
