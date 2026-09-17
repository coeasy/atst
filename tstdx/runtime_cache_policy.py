# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Runtime cache policy helpers.

Keeps cache behaviour aligned with Provider isolation.  Cache decisions are
made from error semantics, not from transport availability alone.
"""

from __future__ import annotations

from typing import Any

_RETRYABLE_ERROR_NAMES = frozenset(
    {
        "TimeoutError",
        "ConnectionError",
        "OSError",
    }
)


def should_negative_cache(error: Any) -> bool:
    """Return whether an error represents a deterministic negative result.

    Transient transport/provider availability failures must not poison a
    Provider cache boundary.
    """

    if error is None:
        return False

    name = type(error).__name__
    if name in _RETRYABLE_ERROR_NAMES:
        return False

    if getattr(error, "retryable", False) is True:
        return False

    context = getattr(error, "context", {}) or {}
    if context.get("retryable") is True:
        return False

    return True
