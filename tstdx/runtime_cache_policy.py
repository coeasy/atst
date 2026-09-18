# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Runtime negative-cache policy aligned with the canonical error taxonomy."""

from __future__ import annotations

from typing import Any

from .errors import SourceUnavailable, TdxError


def should_negative_cache(error: Any) -> bool:
    """Return whether an error is a deterministic terminal tstdx failure.

    Transport/protocol/freshness failures marked retryable must never poison a
    Provider cache boundary. Unknown third-party exceptions are also excluded;
    DirectProviderExecutor wraps those into the canonical TdxError hierarchy
    before they reach Runtime.
    """

    if not isinstance(error, TdxError):
        return False
    if isinstance(error, SourceUnavailable):
        return False
    return not error.advice.retryable
