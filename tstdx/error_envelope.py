# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical safe error envelope for Python/REST/WS/MCP boundaries."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import TdxError, advice_for

__all__ = ["ErrorEnvelope", "to_error_envelope", "safe_context"]

_SENSITIVE_FRAGMENTS = (
    "token",
    "cookie",
    "authorization",
    "password",
    "secret",
    "credential",
    "header",
    "payload",
    "traceback",
    "stack",
    "absolute_path",
)


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:509] + "..."
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in list(value)[:20]]
    if isinstance(value, Mapping):
        return safe_context(value)
    return type(value).__name__


def safe_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Remove credential/raw-payload style fields from public diagnostics."""
    output: dict[str, Any] = {}
    for key, value in dict(context or {}).items():
        normalized = str(key).strip().lower()
        if any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS):
            continue
        output[str(key)] = _safe_value(value)
    return output


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    code: str
    type: str
    message: str
    phase: str
    capability: str | None = None
    provider: str | None = None
    channel: str | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    query_id: str | None = None
    retryable: bool = False
    retry_after: float | None = None
    partial: bool = False
    alternatives: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "type": self.type,
            "message": self.message,
            "phase": self.phase,
            "capability": self.capability,
            "provider": self.provider,
            "channel": self.channel,
            "request_id": self.request_id,
            "query_id": self.query_id,
            "retryable": self.retryable,
            "retry_after": self.retry_after,
            "partial": self.partial,
            "alternatives": list(self.alternatives),
            "context": dict(self.context),
        }


def to_error_envelope(
    exc: BaseException,
    *,
    phase: str | None = None,
    request_id: str | None = None,
    query_id: str | None = None,
    provider: str | None = None,
    channel: str | None = None,
    capability: str | None = None,
) -> ErrorEnvelope:
    """Map any exception to one safe public contract.

    Native exceptions intentionally collapse to E9000/internal error. Detailed
    native exception text belongs in server logs, not public responses.
    """
    if isinstance(exc, TdxError):
        context = safe_context(exc.context)
        advice = advice_for(exc)
        alternatives_raw = context.pop("alternatives", ())
        if isinstance(alternatives_raw, str):
            alternatives = (alternatives_raw,)
        elif isinstance(alternatives_raw, (list, tuple)):
            alternatives = tuple(str(item) for item in alternatives_raw[:20])
        else:
            alternatives = ()
        retry_after_value = context.get("retry_after")
        retry_after = (
            float(retry_after_value)
            if isinstance(retry_after_value, (int, float))
            else None
        )
        terminal = bool(context.get("terminal", False))
        retry_same_provider = context.get("retry_same_provider")
        retryable = (
            bool(retry_same_provider)
            if retry_same_provider is not None
            else bool(advice.retryable)
        )
        if terminal:
            retryable = False
        return ErrorEnvelope(
            code=exc.code,
            type=type(exc).__name__,
            message=exc.message or type(exc).__name__,
            phase=phase or str(context.get("phase") or "execution"),
            capability=capability or _optional_str(context.get("capability")),
            provider=provider or _optional_str(context.get("provider")),
            channel=channel or _optional_str(context.get("channel")),
            request_id=request_id or uuid.uuid4().hex,
            query_id=query_id or _optional_str(context.get("query_id")),
            retryable=retryable,
            retry_after=retry_after,
            partial=bool(context.get("partial", False)),
            alternatives=alternatives,
            context=context,
        )

    return ErrorEnvelope(
        code="E9000",
        type="InternalError",
        message="internal error",
        phase=phase or "execution",
        capability=capability,
        provider=provider,
        channel=channel,
        request_id=request_id or uuid.uuid4().hex,
        query_id=query_id,
        retryable=False,
        context={},
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
