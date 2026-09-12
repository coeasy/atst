from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..cache_semantic import SemanticResultCache
from ..provider import LocalProvider, TdxProvider, WebProvider

if TYPE_CHECKING:
    from .runtime import Runtime


def create_runtime(
    *,
    tdx: Any | None = None,
    web: Mapping[str, Any] | None = None,
    local: Any | None = None,
    provider_order: Sequence[str] | None = None,
    semantic_cache: SemanticResultCache | None = None,
    default_cache_ttl: float | None = None,
) -> Runtime:
    """Create a V14 runtime from explicitly injected execution backends.

    ``web`` maps canonical Provider ids (for example ``eastmoney`` or
    ``tencent``) to source objects. ``semantic_cache`` is the existing
    provenance-preserving cache keyed by canonical ``QueryFingerprint``; it is
    never registered as a Provider.
    """
    from .runtime import Runtime

    runtime = Runtime(
        provider_order=provider_order,
        semantic_cache=semantic_cache,
        default_cache_ttl=default_cache_ttl,
    )
    if local is not None:
        runtime.register_provider(LocalProvider(local))
    if tdx is not None:
        runtime.register_provider(TdxProvider(tdx))
    for provider_id, source in dict(web or {}).items():
        runtime.register_provider(WebProvider(provider_id, source))
    return runtime
