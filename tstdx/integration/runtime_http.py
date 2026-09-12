# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Strict Provider-first HTTP v2 surface.

Legacy ``integration.http_server.create_app`` remains compatible. This v2 app is
small by design: canonical quotes/bars go through UnifiedRuntime and every normal
failure uses ErrorEnvelope.
"""

from __future__ import annotations

from typing import Any

from ..error_envelope import to_error_envelope
from ..runtime import UnifiedRuntime

__all__ = ["create_runtime_app"]


def _serialize_result(result: Any) -> dict[str, Any]:
    data = result.data
    if isinstance(data, list):
        data = [item.to_dict() if hasattr(item, "to_dict") else item for item in data]
    elif hasattr(data, "to_dict"):
        data = data.to_dict()
    meta = result.meta
    provenance = meta.provenance
    return {
        "data": data,
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
        from fastapi import FastAPI, Query, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("HTTP 服务需要安装可选依赖: pip install tstdx[server]") from exc

    rt = runtime or UnifiedRuntime()
    app = FastAPI(title="tstdx Provider-first Runtime", version="2")

    @app.exception_handler(Exception)
    async def _runtime_error(request: Request, exc: Exception) -> JSONResponse:
        envelope = to_error_envelope(exc)
        return JSONResponse(
            status_code=envelope.http_status,
            content={"error": envelope.to_dict()},
        )

    @app.get("/v2/quotes")
    def quotes(
        symbols: str = Query(..., min_length=1),
        provider: str = "tdx",
        max_age: float | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        values = tuple(item.strip() for item in symbols.split(",") if item.strip())
        if not values:
            from ..errors import ValidationError

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
