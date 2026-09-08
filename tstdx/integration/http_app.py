# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Official FastAPI application factory for the v12 planned runtime.

The legacy :mod:`tstdx.integration.http_server` module still contains the stable
43-route declaration. This factory reuses those routes but injects the actual
v12 runtime dependencies before route construction:

* QueryPlan-backed :class:`UnifiedMarketDataService`;
* Provider-bound compatibility client;
* Future-based bounded TaskStore v2;
* canonical safe ErrorEnvelope + request id handling.

No request is allowed to fall back to another Provider implicitly.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from ..error_envelope import to_error_envelope
from ..errors import TdxError, http_status_for
from ..planned_service import UnifiedMarketDataService
from . import http_server as _routes
from .http_runtime import ProviderHttpClient
from .tasks import TaskStore as _SafeTaskStore
from .tasks import TaskStoreFull as _SafeTaskStoreFull

__all__ = ["create_app", "PlannedProviderHttpClient", "PlannedTaskStore"]

_LOG = logging.getLogger(__name__)


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
            raise _routes.TaskStoreFull(str(exc)) from exc


def create_app(client: Any = None) -> Any:  # noqa: ANN401
    """Build the official REST app on the planned Provider runtime."""
    runtime_client = client if client is not None else PlannedProviderHttpClient()

    legacy_task_store = _routes.TaskStore
    _routes.TaskStore = PlannedTaskStore  # type: ignore[assignment]
    try:
        app = _routes.create_app(runtime_client)
    finally:
        _routes.TaskStore = legacy_task_store  # type: ignore[assignment]

    app.state.runtime = "planned-v12"

    try:
        from fastapi import Request
        from fastapi.responses import JSONResponse
    except ImportError:  # pragma: no cover - legacy factory already reports this
        Request = Any  # type: ignore[misc,assignment]
        JSONResponse = Any  # type: ignore[misc,assignment]

    @app.middleware("http")
    async def _request_identity(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(TdxError)
    async def _planned_tdx_error(request: Request, exc: TdxError) -> JSONResponse:
        envelope = to_error_envelope(
            exc,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=http_status_for(exc),
            content={"error": envelope.to_dict()},
        )

    @app.exception_handler(Exception)
    async def _planned_internal_error(request: Request, exc: Exception) -> JSONResponse:
        _LOG.error(
            "unhandled REST error request_id=%s path=%s",
            getattr(request.state, "request_id", None),
            request.url.path,
            exc_info=exc,
        )
        envelope = to_error_envelope(
            exc,
            phase="http",
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(status_code=500, content={"error": envelope.to_dict()})

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
