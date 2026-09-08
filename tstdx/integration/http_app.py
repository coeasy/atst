# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Official FastAPI application factory for the v12 planned runtime.

The legacy :mod:`tstdx.integration.http_server` module still contains the stable
43-route declaration. This factory reuses those routes but injects the actual
v12 runtime dependencies before route construction and adds explicit Provider
endpoints for canonical common capabilities.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from ..batch import BatchResult
from ..error_envelope import to_error_envelope
from ..errors import TdxError, ValidationError, http_status_for
from ..planned_service import UnifiedMarketDataService
from ..providers import PROVIDERS
from ..service import QueryResult
from . import http_server as _routes
from .http_runtime import ProviderHttpClient
from .tasks import TaskStore as _SafeTaskStore
from .tasks import TaskStoreFull as _SafeTaskStoreFull

__all__ = ["create_app", "PlannedProviderHttpClient", "PlannedTaskStore"]

_LOG = logging.getLogger(__name__)
_MAX_PROVIDER_SYMBOLS = 1000
_ROUTE_INJECTION_LOCK = threading.Lock()


class PlannedProviderHttpClient(ProviderHttpClient):
    """Old-client-shaped REST adapter backed by the planned service."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], UnifiedMarketDataService] | None = None,
    ) -> None:
        super().__init__(service_factory=service_factory or UnifiedMarketDataService)


class PlannedTaskStore(_SafeTaskStore):
    """TaskStore v2 adapted to the legacy route module's saturation exception."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> str:
        try:
            return super().submit(fn, *args, **kwargs)
        except _SafeTaskStoreFull as exc:
            raise _routes.TaskStoreFull(str(exc)) from exc


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _provider_payload(provider: str) -> dict[str, Any]:
    spec = PROVIDERS.get(provider)
    return {
        "id": spec.id,
        "display_name": spec.display_name,
        "role": spec.role,
        "default": spec.default,
        "channels": [
            {
                "id": channel.id,
                "capabilities": sorted(channel.capabilities),
                "markets": sorted(channel.markets),
                "live": channel.live,
                "local": channel.local,
                "batch_limits": dict(channel.batch_limits),
                "notes": channel.notes,
            }
            for channel in spec.channels
        ],
    }


def create_app(client: Any = None) -> Any:  # noqa: ANN401
    """Build the official REST app on the planned Provider runtime."""
    runtime_client = client if client is not None else PlannedProviderHttpClient()

    # ``http_server`` is retained as a legacy route-definition module and reads
    # its TaskStore from module globals. Keep the temporary compatibility swap
    # serialized so concurrent app construction cannot restore another caller's
    # TaskStore class and create an unbounded/orphan-task runtime.
    with _ROUTE_INJECTION_LOCK:
        legacy_task_store = _routes.TaskStore
        _routes.TaskStore = PlannedTaskStore  # type: ignore[assignment]
        try:
            app = _routes.create_app(runtime_client)
        finally:
            _routes.TaskStore = legacy_task_store  # type: ignore[assignment]

    app.state.runtime = "planned-v12"

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    def _service() -> UnifiedMarketDataService:
        service = getattr(runtime_client, "service", None)
        if not isinstance(service, UnifiedMarketDataService):
            raise HTTPException(
                status_code=501,
                detail="explicit Provider endpoints require the planned runtime client",
            )
        return service

    def _codes(raw: str) -> list[str]:
        codes = [item.strip() for item in str(raw).split(",") if item.strip()]
        if not codes:
            raise ValidationError("codes 不能为空")
        if len(codes) > _MAX_PROVIDER_SYMBOLS:
            raise ValidationError(
                "Provider quotes 单次 symbols 超限",
                context={
                    "symbols": len(codes),
                    "max_symbols": _MAX_PROVIDER_SYMBOLS,
                },
            )
        return codes

    @app.get("/providers", tags=["providers"], summary="Provider registry")
    def providers_registry() -> dict[str, Any]:
        return {
            "default_provider": PROVIDERS.default_provider,
            "providers": [_provider_payload(pid) for pid in PROVIDERS.ids()],
        }

    @app.get("/providers/{provider}", tags=["providers"], summary="Provider capabilities")
    def provider_registry(provider: str) -> dict[str, Any]:
        return _provider_payload(provider)

    @app.get(
        "/providers/{provider}/quotes",
        tags=["providers"],
        summary="Explicit Provider quotes",
    )
    def provider_quotes(
        provider: str,
        codes: str,
        deadline_ms: int = 5000,
        max_age: float | None = None,
    ) -> dict[str, Any]:
        result = _service().quotes(
            _codes(codes),
            provider=provider,
            deadline_ms=deadline_ms,
            max_age=max_age,
            with_meta=True,
        )
        if not isinstance(result, QueryResult):
            raise RuntimeError("planned quotes REST contract violated")
        return {
            "data": _jsonable(result.data),
            "meta": _jsonable(result.meta),
        }

    @app.get(
        "/providers/{provider}/quotes/batch",
        tags=["providers"],
        summary="Explicit Provider partial quote batch",
    )
    def provider_quotes_batch(
        provider: str,
        codes: str,
        deadline_ms: int = 5000,
    ) -> dict[str, Any]:
        result = _service().quotes_batch(
            _codes(codes),
            provider=provider,
            deadline_ms=deadline_ms,
        )
        if not isinstance(result, BatchResult):
            raise RuntimeError("quotes_batch REST contract violated")
        return _jsonable(result.to_dict())

    @app.get(
        "/providers/{provider}/bars/{symbol}",
        tags=["providers"],
        summary="Explicit Provider bars",
    )
    def provider_bars(
        provider: str,
        symbol: str,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjust: str = "",
        deadline_ms: int = 5000,
        max_age: float | None = None,
    ) -> dict[str, Any]:
        result = _service().bars(
            symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjust=adjust,
            deadline_ms=deadline_ms,
            max_age=max_age,
            with_meta=True,
        )
        if not isinstance(result, QueryResult):
            raise RuntimeError("planned bars REST contract violated")
        return {
            "data": _jsonable(result.data),
            "meta": _jsonable(result.meta),
        }

    @app.middleware("http")
    async def _request_identity(request: Any, call_next: Any) -> Any:  # noqa: ANN401
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(TdxError)
    async def _planned_tdx_error(request: Any, exc: TdxError) -> Any:
        envelope = to_error_envelope(
            exc,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=http_status_for(exc),
            content={"error": envelope.to_dict()},
        )

    @app.exception_handler(Exception)
    async def _planned_internal_error(request: Any, exc: Exception) -> Any:
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
