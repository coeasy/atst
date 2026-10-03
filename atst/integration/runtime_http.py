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
        currentness: str = "historical",
        strict: bool = False,
        start_date: str = "",
        end_date: str = "",
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
                currentness=currentness,
                strict=strict,
                start_date=start_date,
                end_date=end_date,
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

    @app.get("/v13/universe")
    def universe_classes() -> dict[str, Any]:
        """标的类别表——「按类别取清单」这张面的自述。

        与 ``atst universe --list-class --json``（CLI 面）出自同一份
        :data:`atst.universe.ASSET_CLASSES`，不各写一份。

        这里**不是** capability：capability 表走 ``QuerySpec → QueryPlan → runtime``
        那条行情读取链路（登记要动能力口径 + provider 显式声明），而"有哪些标的"
        是工具方法，理由写在 :mod:`atst.universe` 的模块 docstring 里。
        """
        from ..universe import ASSET_CLASSES, NODE_TO_CLASSES, SINA_NODES

        return {
            "api": "v13",
            "classes": [
                {
                    "kind": item.name,
                    "label": item.label,
                    "sina_node": item.sina_node,
                    "segments": list(item.tdx_segments),
                    "index_bit": item.index_bit,
                    "directory": item.directory,
                }
                for item in ASSET_CLASSES
            ],
            "sina_nodes": [{"node": node, "label": label} for node, label in SINA_NODES],
            "node_to_classes": {node: list(kinds) for node, kinds in NODE_TO_CLASSES.items()},
        }

    @app.get("/v13/universe/{kind}")
    def universe(
        kind: str,
        root: str = "data",
        limit: int | None = Query(None, ge=1),
    ) -> dict[str, Any]:
        """``GET /v13/universe/{kind}``：本地代码表里某一类（或 ``all``）的全部标的。

        **这张面只读本地代码表**，``source`` 恒为 ``table``——这是刻意的：

        * 新浪那一级是秒级的**外呼**，tdx 那一级是"枚举段 + 逐只探"的**分钟级**作业；
          在 HTTP 请求里同步跑一分钟，等于把一个后台批任务塞进了请求路径（网关会被
          占住，超时与重试语义也全乱）。要刷新清单就跑 ``scripts/sync_daily_history.py
          --fetch-list``（盘后批任务），网关只负责把**已经落好的表**发出去。
        * 副作用是这条路由**没有任何外呼**：给定 ``root`` 与 ``kind``，结果完全确定，
          同参数重复调用与并发调用都得到同一份数据。

        ``root`` 是代码表所在目录（默认 ``data``，即 ``data/universe.csv``）；表不存在
        时返回 ``count: 0`` 并附一句 ``hint``，而不是报错——"还没跑过同步"是部署常态，
        不是请求错误。
        """
        from pathlib import Path

        from ..errors import ValidationError as _ValidationError
        from ..universe import ASSET_CLASSES, class_of, from_table

        try:
            # 类别名只在这里判一次（``class_of`` 抛 KeyError，翻成 E1010）；这一层
            # 不再自己抄一份合法名单，那会是第二份会过期的事实源。
            kinds = (
                tuple(item.name for item in ASSET_CLASSES)
                if kind == "all"
                else (class_of(kind).name,)
            )
        except KeyError as exc:
            raise _ValidationError(str(exc), context={"phase": "universe"}) from exc
        rows = [] if not root else [row for name in kinds for row in from_table(Path(root), name)]
        if limit:
            rows = rows[:limit]
        payload: dict[str, Any] = {
            "api": "v13",
            "kind": kind,
            "source": "table",
            "count": len(rows),
            "items": [{"code": row.code, "name": row.name, "kind": row.kind} for row in rows],
        }
        if not rows:
            payload["hint"] = (
                f"{root or '（root 为空）'}/universe.csv 里没有 {kind} 的标的；"
                "先跑 python scripts/sync_daily_history.py --fetch-list 生成代码表"
            )
        return payload

    @app.get("/v13/runtime/health")
    def health() -> dict[str, Any]:
        rt = api.runtime
        core = api.core_capability_statuses()
        return {
            "status": "ok",
            "api": "v13",
            "default_provider": rt.planner.default_provider,
            "direct_bindings": len(DIRECT_BINDINGS),
            "migrated_capabilities": len(api.capabilities()),
            "core_unavailable": sorted(
                capability for capability, state in core.items() if not state["default_available"]
            ),
        }

    return app
