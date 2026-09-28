# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical Provider-first HTTP v13 surface."""

# 这里刻意**不**加 ``from __future__ import annotations``：FastAPI 按名字从模块全局解析
# 形参注记，而 ``Request`` 是可选依赖、只在工厂内部导入。字符串化之后它解析不出来，
# 就会被当成一个必填查询参数——实测每个请求都 422（``loc: ["query", "request"]``）。
# 注记保持求值，本地导入的类在嵌套 def 执行时仍在作用域里。
from contextlib import asynccontextmanager, suppress
from typing import Any

from ..client.api import Client
from ..error_envelope import to_error_envelope
from ..errors import ValidationError
from ..providers import PROVIDERS
from ..runtime.executor import DIRECT_BINDINGS
from ..runtime.orchestration import FallbackPolicy
from .serialization import serialize_result
from .wire_fields import QUERY_BODY_FIELDS, reject_reserved_kwargs, reject_undeclared

__all__ = ["create_runtime_app"]


def create_runtime_app(client: Client | None = None) -> Any:
    try:
        from fastapi import Depends, FastAPI, HTTPException, Query, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("HTTP 服务需要安装可选依赖: pip install atst[server]") from exc

    owns_client = client is None
    api = client or Client()

    def _no_undeclared_query(request: Request) -> None:
        """查询串的白名单就是路由签名本身，因此这里没有第二份名单可过期。

        未声明的键过去无人过问（``?max_age=0`` 返回 200），F-47 的裁决是让它当场 422。
        """
        route = request.scope["route"]
        reject_undeclared(
            face="http_query",
            where=f"{request.method} {request.url.path}",
            declared=[param.name for param in route.dependant.query_params],
            received=request.query_params.keys(),
        )

    @asynccontextmanager
    async def _lifespan(_app: Any) -> Any:
        """应用生命周期收尾：关掉本工厂自己造的那份 ``Client``。

        ``create_runtime_app`` 过去只是 ``return app``，自己造的那份内核没有任何释放
        入口（第 26 轮 F-100）。这条链今天**不**释放 socket 或线程：``Client`` 跨调用不
        持有连接，内置执行器连 ``close()`` 都没有（实测口径见
        :file:`tests/runtime/test_close_chain_ownership.py`）。它兑现的是所有权协议——
        传入的那份归调用方所有，这里不关（与 MCP 面 ``_owns_client`` 同一口径）——以及
        注入执行器（自带池、心跳线程或别的外设）那条形同协议的口子。

        收尾写在 ``finally`` 而不是 ``yield`` 后面：生命周期体里任何一次抛错都会从
        ``yield`` 处穿出这个生成器，写在后面的那两行根本不会被执行——和执行器那条
        ``with`` 同一族（第 31 轮 31-C，判据
        :file:`tests/runtime/test_runtime_http_client_release.py`）。
        """
        try:
            yield
        finally:
            if owns_client:
                with suppress(Exception):
                    api.close()

    app = FastAPI(
        title="atst v13 Runtime",
        version="13",
        dependencies=[Depends(_no_undeclared_query)],
        lifespan=_lifespan,
    )

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
            "core": Client.core_capability_statuses(),
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
        # body 不经查询串那道闸：它是 payload 这一个 dict，键由本函数自己挑。
        reject_undeclared(
            face="http_body",
            where=f"POST /v13/query/{capability}",
            declared=QUERY_BODY_FIELDS,
            received=payload.keys(),
        )
        args = payload.get("args", [])
        kwargs = payload.get("kwargs", {})
        if not isinstance(args, list):
            raise ValidationError("args must be an array")
        if not isinstance(kwargs, dict):
            raise ValidationError("kwargs must be an object")
        reject_reserved_kwargs(
            face="http_body",
            where=f"POST /v13/query/{capability} 的 kwargs",
            kwargs=kwargs,
        )
        return serialize_result(
            api.call(
                capability,
                *args,
                provider=payload.get("provider"),
                channel=payload.get("channel"),
                currentness=str(payload.get("currentness", "business")),
                **kwargs,
            )
        )

    @app.get("/v13/quotes")
    def quotes(
        symbols: str = Query(..., min_length=1),
        provider: str | None = None,
        fallback: str | None = None,
    ) -> dict[str, Any]:
        values = tuple(item.strip() for item in symbols.split(",") if item.strip())
        if not values:
            raise ValidationError("symbols 不能为空")
        return serialize_result(
            api.quotes(
                values,
                provider=provider,
                policy=FallbackPolicy.from_wire(fallback),
                currentness="live",
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
    ) -> dict[str, Any]:
        return serialize_result(
            api.bars(
                symbol,
                provider=provider,
                policy=FallbackPolicy.from_wire(fallback),
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness="historical",
            )
        )

    @app.get("/v13/snapshot/{symbol}")
    def snapshot(symbol: str, provider: str = "tdx") -> dict[str, Any]:
        return serialize_result(api.snapshot(symbol, provider=provider))

    @app.get("/v13/minute/{symbol}")
    def minute(symbol: str, provider: str | None = None) -> dict[str, Any]:
        return serialize_result(api.minute(symbol, provider=provider))

    @app.get("/v13/trades/{symbol}")
    def trades(
        symbol: str,
        provider: str | None = None,
        start: int = Query(0, ge=0),
        count: int = Query(0, ge=0),
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
        core = Client.core_capability_statuses()
        return {
            "status": "ok",
            "api": "v13",
            "default_provider": rt.planner.default_provider,
            "direct_bindings": len(DIRECT_BINDINGS),
            "migrated_capabilities": len(api.capabilities()),
            "core_unavailable": sorted(
                capability for capability, state in core.items() if not state["available"]
            ),
        }

    return app
