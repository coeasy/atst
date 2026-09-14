# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Application-level failure decisions for one already-selected Provider.

Errors describe *what happened*. ``FailurePolicy`` decides what an application
may do next. The policy has no switch-provider action: v12 never automatically
executes a different Provider after failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import (
    CommandOffline,
    IntegrityViolation,
    SourceDeprecated,
    SourceUnavailable,
    TdxError,
    ValidationError,
    advice_for,
)
from .execution import ExecutionBudget

__all__ = ["FailureDisposition", "FailurePolicy", "DEFAULT_FAILURE_POLICY"]


@dataclass(frozen=True, slots=True)
class FailureDisposition:
    """Allowed next action inside the selected Provider boundary."""

    retry_same_provider: bool
    switch_host: bool
    retry_after: float | None
    terminal: bool
    reason: str
    provider_switch_allowed: bool = field(default=False, init=False)

    def to_context(self) -> dict[str, object]:
        return {
            "retry_same_provider": self.retry_same_provider,
            "switch_host": self.switch_host,
            "retry_after": self.retry_after,
            "terminal": self.terminal,
            "failure_reason": self.reason,
            "provider_switch_allowed": False,
        }


class FailurePolicy:
    """Translate stable error/advice facts into one Provider-bound disposition."""

    _TERMINAL = (ValidationError, CommandOffline, IntegrityViolation, SourceDeprecated)

    def decide(
        self,
        exc: BaseException,
        *,
        budget: ExecutionBudget | None = None,
    ) -> FailureDisposition:
        if not isinstance(exc, TdxError):
            return FailureDisposition(
                retry_same_provider=False,
                switch_host=False,
                retry_after=None,
                terminal=True,
                reason="unclassified_internal_error",
            )

        if isinstance(exc, self._TERMINAL):
            return FailureDisposition(
                retry_same_provider=False,
                switch_host=False,
                retry_after=None,
                terminal=True,
                reason=type(exc).__name__,
            )

        if isinstance(exc, SourceUnavailable):
            return FailureDisposition(
                retry_same_provider=False,
                switch_host=False,
                retry_after=None,
                terminal=True,
                reason="selected_provider_unavailable",
            )

        advice = advice_for(exc)
        retryable = bool(advice.retryable)
        switch_host = bool(advice.switch_host and retryable)
        retry_after = float(advice.backoff) if retryable and advice.backoff > 0 else None

        if budget is not None and budget.remaining_ns() <= 0:
            return FailureDisposition(
                retry_same_provider=False,
                switch_host=False,
                retry_after=None,
                terminal=True,
                reason="query_deadline_exhausted",
            )

        return FailureDisposition(
            retry_same_provider=retryable,
            switch_host=switch_host,
            retry_after=retry_after,
            terminal=not retryable,
            reason=type(exc).__name__,
        )


DEFAULT_FAILURE_POLICY = FailurePolicy()
