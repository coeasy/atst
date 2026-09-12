# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider-first facade entrypoint.

Legacy ``UnifiedQuoteAPI`` remains available for compatibility. New strict
callers should use :func:`runtime_api`, which binds every request to exactly one
Provider/Channel plan and never performs implicit cross-Provider fallback.
"""

from __future__ import annotations

from typing import Any

from ..runtime import UnifiedRuntime

__all__ = ["runtime_api", "UnifiedRuntime"]


def runtime_api(**kwargs: Any) -> UnifiedRuntime:
    return UnifiedRuntime(**kwargs)
