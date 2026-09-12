from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..provider import CacheProvider, LocalProvider, TdxProvider, WebProvider
from .runtime import Runtime


def create_runtime(
    *,
    tdx: Any | None = None,
    web: Any | None = None,
    local: Any | None = None,
    cache: Any | None = None,
    provider_order: Sequence[str] | None = None,
) -> Runtime:
    """Create a v14 runtime without introducing eager optional dependencies.

    Backends are injected explicitly. This keeps the core package zero-dependency
    and lets existing clients/readers migrate behind the runtime incrementally.
    """
    runtime = Runtime(provider_order=provider_order)
    if cache is not None:
        runtime.register_provider(CacheProvider(cache))
    if local is not None:
        runtime.register_provider(LocalProvider(local))
    if tdx is not None:
        runtime.register_provider(TdxProvider(tdx))
    if web is not None:
        runtime.register_provider(WebProvider(web))
    return runtime
