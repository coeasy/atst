# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical safe error envelope shared by all external surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .errors import TdxError, http_status_for

__all__ = ["ErrorEnvelope", "to_error_envelope"]


_SAFE_CONTEXT_KEYS = frozenset(
    {
        "provider",
        "channel",
        "capability",
        "phase",
        "symbol",
        "period",
        "currentness",
        "retryable",
        "fallback",
        "provider_switch_allowed",
        "request_id",
    }
)


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    code: str
    error: str
    message: str
    http_status: int
    retryable: bool
    context: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "context": dict(self.context),
        }


def _safe_context(context: Mapping[str, Any]) -> Mapping[str, Any]:
    sanitized = {key: context[key] for key in _SAFE_CONTEXT_KEYS if key in context}
    return MappingProxyType(sanitized)


def to_error_envelope(exc: Exception) -> ErrorEnvelope:
    """Normalize a normal Exception without swallowing process-control signals.

    Callers must deliberately catch ``Exception`` rather than ``BaseException``;
    KeyboardInterrupt/SystemExit therefore remain observable process-control
    signals and never become API errors.
    """
    if isinstance(exc, TdxError):
        return ErrorEnvelope(
            code=exc.code,
            error=type(exc).__name__,
            message=exc.message or type(exc).__name__,
            http_status=http_status_for(exc),
            retryable=bool(exc.advice.retryable),
            context=_safe_context(exc.context),
        )
    return ErrorEnvelope(
        code="E9000",
        error="InternalError",
        message="internal error",
        http_status=500,
        retryable=False,
        context=MappingProxyType({}),
    )
