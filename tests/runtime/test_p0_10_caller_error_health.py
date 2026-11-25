# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P0-10: caller-side errors must not poison provider auto-health."""

from __future__ import annotations

import pytest

from atst.errors import FreshnessViolation, TransportError, ValidationError
from atst.query import QuerySpec
from atst.runtime import kernel
from atst.runtime.kernel import UnifiedRuntime


class _RaisingExecutor:
    """Injected executor that always raises the configured exception."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def execute(self, plan):
        raise self.exc


def _install_health_spy(monkeypatch, calls: list[tuple[str, str, str]]) -> None:
    class Spy:
        def record_failure(self, provider: str, *, code: str = "") -> None:
            calls.append(("failure", provider, code))

        def record_success(self, provider: str) -> None:
            calls.append(("success", provider, ""))

    monkeypatch.setattr(kernel, "provider_health", lambda: Spy())


@pytest.mark.parametrize(
    "exc",
    [ValidationError("bad caller option"), FreshnessViolation("unproven currentness")],
)
def test_caller_side_errors_are_reraised_without_health_penalty(
    monkeypatch, exc: Exception
) -> None:
    calls: list[tuple[str, str, str]] = []
    _install_health_spy(monkeypatch, calls)
    runtime = UnifiedRuntime(executor=_RaisingExecutor(exc))
    spec = QuerySpec.build("quotes", symbols="sh600000", provider="tdx")

    with pytest.raises(type(exc)):
        runtime.execute(spec)

    assert calls == []
    assert not calls, "caller-side 4xx must not write provider health"


def test_genuine_provider_failure_is_still_recorded(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    _install_health_spy(monkeypatch, calls)
    runtime = UnifiedRuntime(executor=_RaisingExecutor(TransportError("socket broke")))
    spec = QuerySpec.build("quotes", symbols="sh600000", provider="tdx")

    with pytest.raises(TransportError):
        runtime.execute(spec)

    assert len(calls) == 1
    kind, provider, code = calls[0]
    assert kind == "failure"
    assert provider == "tdx"
    assert code, "genuine failure must keep its auditable error code"


def test_successful_request_still_records_success(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []
    _install_health_spy(monkeypatch, calls)

    from atst.result import Provenance, QueryResult

    class OkExecutor:
        def execute(self, plan):
            return QueryResult.from_plan([], plan=plan, provenance=Provenance.direct(plan))

    runtime = UnifiedRuntime(executor=OkExecutor())
    spec = QuerySpec.build("quotes", symbols="sh600000", provider="tdx")
    runtime.execute(spec)

    assert calls == [("success", "tdx", "")]
