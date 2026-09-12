# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Strict Provider-first HTTP v2 surface."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..runtime import UnifiedRuntime

__all__ = ["create_runtime_app"]


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _serialize_result(result: Any) -> dict[str, Any]:
    meta = result.meta
    provenance = meta.provenance
    return {
        "data": _jsonable(result.data),
        "meta": {
            "provider": meta.provider,
            "channel": meta.channel,
            "capability": meta.capability,
            "fingerprint": meta.fingerprint,
            "provenance": {
                "kind": provenance.kind.value,
                "observed_at_ns": provenance.observed_at_ns,
                "cache_tier": provenance.cache_tier,
                "fallback": provenance.fallback,
            },
        },
    }


def create_runtime_app(runtime: UnifiedRuntime | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("HTTP 服务需要安装可选依赖: pip install tstdx[server]") from exc

    rt = runtime or UnifiedRuntime()
    app = FastAPI(title="tstdx Provider-first Runtime", version="2")

    def _response(exc: Exception) -> JSONResponse:
        envelope = to_error_envelope(exc)
        return JSONResponse(
            status_code=envelope.http_status,
            content={"error": envelope.to_dict()},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request
        return _response(
            ValidationError(
                "request validation failed",
                context={"phase": "http_validation"},
                cause=exc,
            )
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        error = ValidationError(
            "http request rejected",
            context={"phase": "http_framework"},
            cause=exc,
        )
        envelope = to_error_envelope(error)
        return JSONResponse(
            status_code=int(exc.status_code),
            content={"error": envelope.to_dict()},
        )

    @app.exception_handler(Exception)
    async def _runtime_error(request: Request, exc: Exception) -> JSONResponse:
        del request
        return _response(exc)

    @app.get("/v2/quotes")
    def quotes(
        symbols: str = Query(..., min_length=1),
        provider: str = "tdx",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        values = tuple(item.strip() for item in symbols.split(",") if item.strip())
        if not values:
            raise ValidationError("symbols 不能为空")
        return _serialize_result(
            rt.quotes(
                values,
                provider=provider,
                currentness="live",
                max_age=max_age,
                use_cache=use_cache,
            )
        )

    @app.get("/v2/bars/{symbol}")
    def bars(
        symbol: str,
        provider: str = "tdx",
        period: str = "day",
        count: int = Query(320, ge=1, le=10000),
        start: int = Query(0, ge=0),
        adjustment: str = "",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        return _serialize_result(
            rt.bars(
                symbol,
                provider=provider,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness="historical",
                max_age=max_age,
                use_cache=use_cache,
            )
        )

    @app.get("/v2/runtime/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "default_provider": rt.planner.default_provider,
            "direct_bindings": len(rt.executor._bindings),
        }

    return app
