# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical Provider-first HTTP v13 surface."""

from __future__ import annotations

from typing import Any

from ..client_api import Client
from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..providers import PROVIDERS
from ..runtime.executor import DIRECT_BINDINGS
from ..runtime.orchestration import FallbackPolicy
from .serialization import serialize_result

__all__ = ["create_runtime_app"]


def _policy(value: str | None) -> FallbackPolicy | None:
    if value is None or not value.strip():
        return None
    providers = tuple(item.strip() for item in value.split(",") if item.strip())
    return FallbackPolicy.build(*providers)


def create_runtime_app(client: Client | None = None) -> Any:
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("HTTP 服务需要安装可选依赖: pip install tstdx[server]") from exc

    api = client or Client()
    app = FastAPI(title="tstdx v13 Runtime", version="13")

    def _response(exc: Exception) -> JSONResponse:
        envelope = to_error_envelope(exc)
        return JSONResponse(status_code=envelope.http_status, content={"error": envelope.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request
        return _response(
            ValidationError(
                "request validation failed", context={"phase": "http_validation"}, cause=exc
            )
        )

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        error = ValidationError(
            "http request rejected", context={"phase": "http_framework"}, cause=exc
        )
        envelope = to_error_envelope(error)
        return JSONResponse(status_code=int(exc.status_code), content={"error": envelope.to_dict()})

    @app.exception_handler(Exception)
    async def _runtime_error(request: Request, exc: Exception) -> JSONResponse:
        del request
        return _response(exc)

    @app.get("/v13/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "capabilities": list(api.capabilities()),
            "providers": {
                provider: {
                    channel.id: sorted(channel.capabilities)
                    for channel in PROVIDERS.get(provider).channels
                }
                for provider in PROVIDERS.ids()
            },
        }

    @app.post("/v13/query/{capability}")
    def query_capability(capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        args = payload.get("args", [])
        kwargs = payload.get("kwargs", {})
        if not isinstance(args, list):
            raise ValidationError("args must be an array")
        if not isinstance(kwargs, dict):
            raise ValidationError("kwargs must be an object")
        return serialize_result(
            api.call(
                capability,
                *args,
                provider=payload.get("provider"),
                channel=payload.get("channel"),
                currentness=str(payload.get("currentness", "business")),
                max_age=payload.get("max_age"),
                **kwargs,
            )
        )

    @app.get("/v13/quotes")
    def quotes(
        symbols: str = Query(..., min_length=1),
        provider: str | None = None,
        fallback: str | None = None,
        max_age: float | None = None,
    ) -> dict[str, Any]:
        values = tuple(item.strip() for item in symbols.split(",") if item.strip())
        if not values:
            raise ValidationError("symbols 不能为空")
        return serialize_result(
            api.quotes(
                values,
                provider=provider,
                policy=_policy(fallback),
                currentness="live",
                max_age=max_age,
            )
        )

    @app.get("/v13/bars/{symbol}")
    def bars(
        symbol: str,
        provider: str | None = None,
        fallback: str | None = None,
        period: str = "day",
        count: int = Query(320, ge=1, le=10000),
        start: int = Query(0, ge=0),
        adjustment: str = "",
        max_age: float | None = None,
    ) -> dict[str, Any]:
        return serialize_result(
            api.bars(
                symbol,
                provider=provider,
                policy=_policy(fallback),
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness="historical",
                max_age=max_age,
            )
        )

    @app.get("/v13/snapshot/{symbol}")
    def snapshot(symbol: str, provider: str = "tdx") -> dict[str, Any]:
        return serialize_result(api.snapshot(symbol, provider=provider))

    @app.get("/v13/minute/{symbol}")
    def minute(symbol: str, provider: str = "tdx") -> dict[str, Any]:
        return serialize_result(api.minute(symbol, provider=provider))

    @app.get("/v13/trades/{symbol}")
    def trades(
        symbol: str, provider: str = "tdx", start: int = Query(0, ge=0), count: int = Query(0, ge=0)
    ) -> dict[str, Any]:
        return serialize_result(api.trades(symbol, provider=provider, start=start, count=count))

    @app.get("/v13/security/count")
    def security_count(market: str = "0", provider: str = "tdx") -> dict[str, Any]:
        return serialize_result(api.security_count(market=market, provider=provider))

    @app.get("/v13/security/list")
    def security_list(
        market: str = "0", start: int = Query(0, ge=0), provider: str = "tdx"
    ) -> dict[str, Any]:
        return serialize_result(api.security_list(market=market, start=start, provider=provider))

    @app.get("/v13/runtime/health")
    def health() -> dict[str, Any]:
        rt = api.runtime
        return {
            "status": "ok",
            "api": "v13",
            "default_provider": rt.planner.default_provider,
            "direct_bindings": len(DIRECT_BINDINGS),
            "migrated_capabilities": len(api.capabilities()),
        }

    return app
