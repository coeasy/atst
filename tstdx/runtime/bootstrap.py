from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..provider import LocalProvider, TdxProvider, WebProvider

if TYPE_CHECKING:
    from .runtime import Runtime


def create_runtime(
    *,
    tdx: Any | None = None,
    web: Mapping[str, Any] | None = None,
    local: Any | None = None,
    provider_order: Sequence[str] | None = None,
) -> Runtime:
    """Create a v14 runtime from explicitly injected execution backends.

    ``web`` must map canonical Provider ids (for example ``eastmoney`` or
    ``tencent``) to their source objects. Cache is intentionally absent here:
    cache tiers preserve the original Provider identity and are integrated via
    the canonical semantic cache layer instead of masquerading as Providers.
    """
    from .runtime import Runtime

    runtime = Runtime(provider_order=provider_order)
    if local is not None:
        runtime.register_provider(LocalProvider(local))
    if tdx is not None:
        runtime.register_provider(TdxProvider(tdx))
    for provider_id, source in dict(web or {}).items():
        runtime.register_provider(WebProvider(provider_id, source))
    return runtime
