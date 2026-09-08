# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Official FastAPI application factory for the v12 planned runtime.

The legacy :mod:`tstdx.integration.http_server` module still contains the stable
43-route declaration.  This factory reuses those route declarations but injects
the actual v12 runtime dependencies before route construction:

* QueryPlan-backed :class:`UnifiedMarketDataService`;
* Provider-bound compatibility client;
* Future-based bounded TaskStore v2.

No request is allowed to fall back to another Provider implicitly.
"""

from __future__ import annotations

from typing import Any, Callable

from ..planned_service import UnifiedMarketDataService
from . import http_server as _routes
from .http_runtime import ProviderHttpClient
from .tasks import TaskStore as _SafeTaskStore
from .tasks import TaskStoreFull as _SafeTaskStoreFull

__all__ = ["create_app", "PlannedProviderHttpClient", "PlannedTaskStore"]


class PlannedProviderHttpClient(ProviderHttpClient):
    """Old-client-shaped REST adapter backed by the planned service."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], UnifiedMarketDataService] | None = None,
    ) -> None:
        super().__init__(service_factory=service_factory or UnifiedMarketDataService)

    def file_download(self, symbol: str, filename: str) -> bytes:
        """Route F10 file download to the TDX F10 Channel, not quotation."""
        return self._tdx.f10.download(symbol, filename)

    def parse_text(self, *args: Any, **kwargs: Any) -> Any:
        """Route F10 text parsing to the same TDX F10 Channel."""
        return self._tdx.f10.parse_text(*args, **kwargs)


class PlannedTaskStore(_SafeTaskStore):
    """TaskStore v2 adapted to the legacy route module's saturation exception."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> str:
        try:
            return super().submit(fn, *args, **kwargs)
        except _SafeTaskStoreFull as exc:
            # Route handlers catch http_server.TaskStoreFull.  Re-raise that exact
            # public compatibility type while retaining the bounded v2 manager.
            raise _routes.TaskStoreFull(str(exc)) from exc


def create_app(client: Any = None) -> Any:  # noqa: ANN401
    """Build the official REST app on the planned Provider runtime.

    ``client`` injection is preserved for tests/advanced callers.  When omitted,
    the default is no longer ``_LazyClient -> TdxClient``; it is one
    :class:`PlannedProviderHttpClient` owning one planned service.
    """
    runtime_client = client if client is not None else PlannedProviderHttpClient()

    # ``http_server.create_app`` resolves TaskStore while constructing the app;
    # the created instance is then closed over by all task routes.  Temporarily
    # replacing the class is therefore sufficient and avoids duplicating 43
    # route declarations.  Restore immediately after construction so module
    # globals remain compatible for direct legacy imports.
    legacy_task_store = _routes.TaskStore
    _routes.TaskStore = PlannedTaskStore  # type: ignore[assignment]
    try:
        app = _routes.create_app(runtime_client)
    finally:
        _routes.TaskStore = legacy_task_store  # type: ignore[assignment]

    app.state.runtime = "planned-v12"

    # Add deterministic resource shutdown.  The legacy route module did not own
    # the injected Provider service, so this factory explicitly closes both
    # runtime client and bounded task pool.
    @app.on_event("shutdown")
    def _shutdown_planned_runtime() -> None:
        tasks = getattr(app.state, "tasks", None)
        close_tasks = getattr(tasks, "close", None)
        if callable(close_tasks):
            close_tasks()
        close_client = getattr(runtime_client, "close", None)
        if callable(close_client):
            close_client()

    return app
