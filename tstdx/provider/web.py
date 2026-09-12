from __future__ import annotations

from typing import Any

from ..providers import PROVIDERS, normalize_provider_id
from .base import Provider


class WebProvider(Provider):
    """Dynamic executor for one canonical HTTP/web Provider.

    ``web`` is deliberately not a Provider identity in tstdx. Callers must bind
    the adapter to a concrete trust boundary such as ``eastmoney``, ``tencent``
    or ``sina``.
    """

    def __init__(self, provider_id: str, source: Any | None = None) -> None:
        canonical = normalize_provider_id(provider_id)
        spec = PROVIDERS.get(canonical)
        if spec.id in {"tdx", "local_vipdoc"}:
            raise ValueError(f"{spec.id!r} is not a web provider")
        self.name = spec.id
        self.source = source

    def supports(self, operation: str) -> bool:
        return (
            self.source is not None
            and PROVIDERS.supports(self.name, operation)
            and callable(getattr(self.source, operation, None))
        )

    def query(self, request: Any) -> Any:
        if self.source is None:
            raise RuntimeError(f"web provider {self.name!r} is not configured")
        operation = getattr(request, "operation", None)
        args = tuple(getattr(request, "args", ()))
        params = dict(getattr(request, "params", {}))
        method = getattr(self.source, operation, None)
        if method is None:
            raise AttributeError(f"provider {self.name!r} does not support operation: {operation}")
        return method(*args, **params)
