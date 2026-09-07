# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP 行情服务（C1）—— FastAPI 应用工厂，8 组 43 个端点。

设计要点
--------
* **客户端注入**：:func:`create_app` 接受任何具备
  ``bars/quotes/finance_info/...`` 方法的对象（同步 :class:`~tstdx.client.TdxClient`
  或任何鸭子类型 fake）。``client=None`` 时使用懒加载代理，
  首次请求才构造真实客户端 —— 便于离线导入/文档生成。
* **零强制依赖**：fastapi 除非安装了 extras 才需要，在 :func:`create_app` 内延迟导入。
* **错误映射**：任何 :class:`~tstdx.errors.TdxError` 经
  :func:`~tstdx.errors.http_status_for` 映射为 HTTP 状态码，
  响应体形如 ``{"error": {"code": "E3201", "message": "..."}}``。
  原生异常对外只回 ``internal error``，细节进日志（V6）。
* **方法白名单**（V2）：``/query`` 与 ``/tasks`` 仅允许派发
  :data:`SAFE_CLIENT_METHODS` 内的公开只读方法——``close`` 等破坏性
  方法一律 403/404 拒绝；任务数与结果体有上限（超限 409 / 截断）。
* **可离线测试**：所有端点都是 ``def``（FastAPI 自动跑线程池），
  注入 fake client 即可覆盖全部路由。

端点一览（43，按 :func:`create_app` 实际注册清点）
--------------
quotes(8)      GET /quotes  /quotes/{symbol}  /snapshot  /block_quotes
               /minute_today/{symbol}  /minute_history/{symbol}  /trade_today/{symbol}
               /volume_price_dist/{symbol}
fundamental(8) GET /finance_info/{symbol}  /capital_changes/{symbol}  /corporate_action/{symbol}
               /auction_snapshot  /file_download  /f10/{symbol}/catalog  /security_list
               /security_count/{market}
search(3)      GET /search  /index_list  /wencai
insight(5)     GET /stock_boards/{symbol}  /ipo  /big_order_flow/{symbol}
               /stock_changes  /hot_rank
system(5)      POST /query  GET /system/health  /system/metrics  /system/specs
               /system/version
goods(4)       GET /goods/bars  /goods/quote  POST /goods/quotes  GET /goods/summary
extended(4)    GET /ex/bars  /ex/quote  /mac/quote  /download
tasks(6)       POST /tasks  GET /tasks  GET /tasks/{task_id}  DELETE /tasks/{task_id}
               GET /tasks/{task_id}/result  POST /tasks/{task_id}/cancel
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..errors import TdxError, http_status_for

__all__ = ["create_app", "TaskStore", "TaskStoreFull", "SAFE_CLIENT_METHODS"]

logger = logging.getLogger(__name__)

#: POST / PUT 请求体大小上限（字节）——超限直接 413，防止提交风暴内存 DoS。
MAX_BODY_BYTES: int = 4 * 1024 * 1024

#: 单次请求标的列表上限（深审 L12）：逗号列表无上限会把 N 个标的放大为
#: N 倍上游请求（资源放大 / DoS 面），quotes/snapshot/auction_snapshot 共用。
_MAX_SYMBOL_LIST: int = 100

# --------------------------------------------------------------------------- #
# 方法白名单（V2）：/query 与 /tasks 只允许派发下列**公开只读**客户端方法。
# `close` / `open` / `request`（原始命令透传）等破坏性或高危方法一律拒绝。
# --------------------------------------------------------------------------- #
SAFE_CLIENT_METHODS: frozenset[str] = frozenset(
    {
        "quotes",
        "quotes_concurrent",
        "quotes_snapshot",
        "snapshot",
        "bars",
        "security_count",
        "security_list",
        "capital_changes",
        "finance_info",
        "minute_today",
        "minute_history",
        "trade_today",
        "block_quotes",
        "file_download",
        "auction_snapshot",
        "volume_price_dist",
        "goods_bars",
        "goods_quote",
        "ex_bars",
        "ex_quote",
        "mac_quote",
        "download",
        "parse_text",
        "f10_catalog",
    }
)


def _real_client_has_method(name: str) -> bool:
    """探测真实 :class:`~tstdx.client.TdxClient` 类上是否存在可调用属性。

    仅做**类级**探测（不实例化、不联网），供 ``_LazyClient`` 的
    ``__getattr__`` 存在性校验复用。
    """
    try:
        from ..client import TdxClient
    except Exception:  # pragma: no cover —— 导入失败时保守拒绝
        return False
    return callable(getattr(TdxClient, name, None))


# --------------------------------------------------------------------------- #
# 懒加载客户端
# --------------------------------------------------------------------------- #
class _LazyClient:
    """延迟构造真实 TdxClient 的代理（首请求时才连接）。

    ``__getattr__`` 带**存在性探测**：仅当名字是真实 ``TdxClient`` 类上
    的可调用属性时才返回调用闭包，未知名直接 ``AttributeError``——
    上层 ``getattr(..., None)`` 据此恢复 404 / 400 语义（修复：此前
    返回任意属性闭包 → ``fn is None`` 永假，404 永远失效）。
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._lock = threading.Lock()

    def _real(self) -> Any:
        with self._lock:
            if self._client is None:
                from ..client import TdxClient

                self._client = TdxClient()
            return self._client

    def __getattr__(self, name: str) -> Callable[..., Any]:
        # 内部属性与真实 TdxClient 上不存在的名字一律 AttributeError
        if name.startswith("_") or not _real_client_has_method(name):
            raise AttributeError(name)
        return self._make_caller(name)

    def _make_caller(self, name: str) -> Callable[..., Any]:
        def _call(*args: Any, **kw: Any) -> Any:
            return getattr(self._real(), name)(*args, **kw)

        return _call


# --------------------------------------------------------------------------- #
# 内存任务存储
# --------------------------------------------------------------------------- #
class TaskStoreFull(RuntimeError):
    """任务存储已满（活跃任务数达到 ``max_tasks``）——HTTP 层映射为 409。"""


# 结果体上限（V2）：防止单个任务结果撑爆内存
_MAX_RESULT_ROWS: int = 1000
_MAX_RESULT_BYTES: int = 1024 * 1024


def _clamp_result(result: Any) -> Any:  # noqa: ANN401
    """钳制任务结果体：超行数截断列表，超字节替换为截断摘要。"""
    if isinstance(result, list) and len(result) > _MAX_RESULT_ROWS:
        result = {
            "truncated": True,
            "total_rows": len(result),
            "rows": result[:_MAX_RESULT_ROWS],
        }
    try:
        size = len(json.dumps(result, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"truncated": True, "note": "unserializable result dropped"}
    if size > _MAX_RESULT_BYTES:
        return {"truncated": True, "note": f"result exceeds {_MAX_RESULT_BYTES} bytes"}
    return result


def _safe_error_message(exc: Exception) -> str:
    """对外安全的错误摘要（V6）：TdxError 属预期错误保留 message，原生异常 generic。"""
    if isinstance(exc, TdxError):
        return f"[{exc.code}] {exc}"
    return "internal error"


class TaskStore:
    """进程内后台任务登记簿（C1 任务组）。

    任务 = 一个可调用对象，提交即开线程执行；结果与状态存内存。
    仅适用于单进程部署；多进程请外接任务队列。

    Parameters
    ----------
    max_tasks : int, optional
        活跃任务上限（pending/running 计数）。达到上限再提交抛
        :class:`TaskStoreFull`（HTTP 层 → 409）；已完结任务超限时
        按 ``submitted_at`` 逐出最早一条以免登记簿无限增长。
    """

    def __init__(self, max_tasks: int = 200) -> None:
        if max_tasks <= 0:
            raise ValueError("max_tasks 必须为正整数")
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.max_tasks = max_tasks

    def submit(self, fn: Callable[..., Any], *args: Any, **kw: Any) -> str:
        with self._lock:
            active = sum(
                1 for rec in self._tasks.values() if rec["status"] in ("pending", "running")
            )
            if active >= self.max_tasks:
                raise TaskStoreFull(f"too many active tasks ({active}); max_tasks={self.max_tasks}")
            task_id = uuid.uuid4().hex[:12]
            # 已完结任务占满登记簿时逐出最早一条（提交不因历史记录被拒）
            if len(self._tasks) >= self.max_tasks:
                finished = sorted(
                    (
                        (tid, rec)
                        for tid, rec in self._tasks.items()
                        if rec["status"] in ("done", "failed", "cancelled")
                    ),
                    key=lambda kv: kv[1]["submitted_at"],
                )
                if finished:
                    del self._tasks[finished[0][0]]
            rec: dict[str, Any] = {
                "task_id": task_id,
                "status": "pending",
                "submitted_at": time.time(),
                "result": None,
                "error": None,
            }
            self._tasks[task_id] = rec

        def _run() -> None:
            with self._lock:
                self._tasks[task_id]["status"] = "running"
            try:
                result = fn(*args, **kw)
                with self._lock:
                    if rec["status"] == "cancelled":
                        # 深审 M18：已取消的任务完成时不回写 done——cancelled
                        # 语义保留，迟到的结果直接丢弃
                        return
                    rec["status"] = "done"
                    rec["result"] = _clamp_result(result)
            except Exception as exc:  # noqa: BLE001 —— 任务失败进状态不入日志
                logger.error("task %s failed: %s", task_id, exc, exc_info=exc)
                with self._lock:
                    if rec["status"] == "cancelled":
                        return
                    rec["status"] = "failed"
                    rec["error"] = _safe_error_message(exc)

        th = threading.Thread(target=_run, name=f"task-{task_id}", daemon=True)
        th.start()
        return task_id

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._tasks.get(task_id)
            return dict(rec) if rec else None

    def cancel(self, task_id: str) -> bool:
        """标记取消（协作式：已开始的线程无法强杀）。"""
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None or rec["status"] in ("done", "failed", "cancelled"):
                return False
            rec["status"] = "cancelled"
            return True

    def delete(self, task_id: str) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            # 深审 M19：列表接口只回摘要（不含 result/error 全文）——
            # result 单条可到 1MB，200 条全量回放可达 200MB 响应；
            # 明细请按 task_id 走 GET /tasks/{id}。
            return [
                {k: v for k, v in rec.items() if k not in ("result", "error")}
                for rec in self._tasks.values()
            ]


# --------------------------------------------------------------------------- #
# 错误处理
# --------------------------------------------------------------------------- #
def _error_body(exc: Exception) -> dict[str, Any]:
    """对外错误体（V6）：TdxError 保留 code+message；原生异常只回 generic。"""
    if isinstance(exc, TdxError):
        return {"error": {"code": exc.code, "message": str(exc)}}
    return {"error": {"code": "E9001", "message": "internal error"}}


def _register_error_handlers(app: Any) -> None:  # noqa: ANN401
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(TdxError)
    async def _tdx_error_handler(request: Request, exc: TdxError) -> JSONResponse:
        return JSONResponse(status_code=http_status_for(exc), content=_error_body(exc))

    @app.exception_handler(Exception)
    async def _generic_handler(request: Request, exc: Exception) -> JSONResponse:
        # 详情只进日志，不外泄异常类型与消息（V6）
        logger.error(
            "http_server unhandled error on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=exc,
        )
        return JSONResponse(status_code=500, content=_error_body(exc))


# --------------------------------------------------------------------------- #
# 应用工厂
# --------------------------------------------------------------------------- #
def create_app(client: Any = None) -> Any:  # noqa: ANN401
    """构造 FastAPI 应用。

    Parameters
    ----------
    client:
        行情客户端（:class:`~tstdx.client.TdxClient` 或鸭子类型 fake）。
        ``None`` 时使用懒加载代理。
    """
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover —— 未装 extras
        raise RuntimeError("HTTP 服务需要安装可选依赖: pip install tstdx[server]") from exc

    app = FastAPI(
        title="tstdx 行情服务",
        version=_version(),
        description="通达信行情协议 HTTP 门面（零强制依赖核心之上）",
    )
    real_client = client if client is not None else _LazyClient()
    tasks = TaskStore()
    app.state.client = real_client
    app.state.tasks = tasks
    _register_error_handlers(app)

    # -- 请求体大小护栏（V2）：Content-Length 超 4MB → 413 ------------------- #
    @app.middleware("http")
    async def _body_size_guard(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        content_length = request.headers.get("content-length", "")
        # 深审 M20：chunked 传输没有 Content-Length，旧护栏完全穿透。
        # 本服务面全部端点均为小型 JSON 请求体，无 chunked 合理场景，
        # 无 Content-Length 且声明 chunked 的请求直接按超限拒绝。
        if not content_length.isdigit():
            if "chunked" in request.headers.get("transfer-encoding", "").lower():
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "E9001",
                            "message": f"payload too large (max {MAX_BODY_BYTES} bytes)",
                        }
                    },
                )
        elif int(content_length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "E9001",
                        "message": f"payload too large (max {MAX_BODY_BYTES} bytes)",
                    }
                },
            )
        return await call_next(request)

    def _call(method: str, *args: Any, **kw: Any) -> Any:
        """统一调用客户端方法；非白名单/不存在的方法 → 404 语义。"""
        if method not in SAFE_CLIENT_METHODS:
            raise HTTPException(status_code=404, detail=f"client method not allowed: {method!r}")
        fn = getattr(real_client, method, None)
        if fn is None or not callable(fn):
            raise HTTPException(status_code=404, detail=f"client has no method {method!r}")
        return fn(*args, **kw)

    def _mkt_sym(market: int, code: str) -> str:
        """市场号 + 裸代码 → 带前缀 symbol（族客户端 split_symbol 语义）。

        深审 S#1：族客户端（GoodsClient/ExMarketClient/MacClient）的签名是
        ``fn(symbol, ...)``，市场取自 symbol 前缀、**没有** ``market=`` 形参——
        旧端点直接透传 ``market=`` 必 TypeError → 500。
        """
        prefix = "sh" if int(market) == 1 else "sz"
        return f"{prefix}{code.strip()}"

    # -- quotes 组（8） ------------------------------------------------------ #
    def _split_codes(raw: str) -> list[str]:
        """逗号分隔标的列表解析（深审 L12：上限 100——无上限会把 N 个标的
        放大为 N 倍上游请求，构成资源放大面）。"""
        codes = [c.strip() for c in raw.split(",") if c.strip()]
        if len(codes) > _MAX_SYMBOL_LIST:
            raise HTTPException(
                status_code=422,
                detail=f"too many codes: {len(codes)} > {_MAX_SYMBOL_LIST}",
            )
        return codes

    @app.get("/quotes", tags=["quotes"], summary="批量实时行情")
    def quotes(codes: str = Query(..., description="逗号分隔，如 600519,000001")):
        return {"data": _call("quotes", _split_codes(codes))}

    @app.get("/quotes/{symbol}", tags=["quotes"], summary="单只实时行情")
    def quote(symbol: str):
        rows = _call("quotes", [symbol])
        return {"data": rows[0] if rows else None}

    @app.get("/snapshot", tags=["quotes"], summary="自选快照")
    def snapshot(codes: str = Query(...)):
        return {"data": _call("snapshot", _split_codes(codes))}

    @app.get("/block_quotes", tags=["quotes"], summary="板块行情")
    def block_quotes(block: str = Query(...)):
        return {"data": _call("block_quotes", block)}

    @app.get("/minute_today/{symbol}", tags=["quotes"], summary="当日分时")
    def minute_today(symbol: str):
        return {"data": _call("minute_today", symbol)}

    @app.get("/minute_history/{symbol}", tags=["quotes"], summary="历史分时")
    def minute_history(symbol: str, date: str = Query(...)):
        return {"data": _call("minute_history", symbol, date)}

    @app.get("/trade_today/{symbol}", tags=["quotes"], summary="当日逐笔")
    def trade_today(symbol: str):
        return {"data": _call("trade_today", symbol)}

    @app.get("/volume_price_dist/{symbol}", tags=["quotes"], summary="量价分布")
    def volume_price_dist(symbol: str):
        return {"data": _call("volume_price_dist", symbol)}

    # -- fundamental 组（6） -------------------------------------------------- #
    @app.get("/finance_info/{symbol}", tags=["fundamental"], summary="财务信息")
    def finance_info(symbol: str):
        return {"data": _call("finance_info", symbol)}

    @app.get("/capital_changes/{symbol}", tags=["fundamental"], summary="股本变迁")
    def capital_changes(symbol: str):
        return {"data": _call("capital_changes", symbol)}

    @app.get("/auction_snapshot", tags=["fundamental"], summary="集合竞价快照")
    def auction_snapshot(codes: str = Query(...)):
        return {"data": _call("auction_snapshot", _split_codes(codes))}

    @app.get("/file_download", tags=["fundamental"], summary="文件下载（专业数据）")
    def file_download(symbol: str = Query(...), filename: str = Query(...)):
        # 深审 S#1：TdxClient.file_download(symbol, filename) 需两个位置参数，
        # 旧端点只传 filename → TypeError → 500。
        return {"data": _call("file_download", symbol, filename)}

    @app.get("/f10/{symbol}/catalog", tags=["fundamental"], summary="F10 栏目目录")
    def f10_catalog(symbol: str):
        # F10 族（7615 文件型）专属能力：客户端须为 F10Client（或具备
        # ``f10_catalog`` 方法的鸭子类型）；默认 TdxClient 懒加载代理不含此方法。
        return {"data": _call("f10_catalog", symbol)}

    @app.get("/security_list", tags=["fundamental"], summary="证券列表")
    def security_list(market: int = Query(...), start: int = 0):
        return {"data": _call("security_list", market, start)}

    @app.get("/security_count/{market}", tags=["fundamental"], summary="证券数量")
    def security_count(market: int):
        return {"data": _call("security_count", market)}

    # -- search / wencai 组（4） ------------------------------------------------ #
    @app.get("/search", tags=["search"], summary="统一证券搜索（拼音/汉字/代码）")
    def search(
        pattern: str = Query(..., description="如 maotai / 茅台 / 600519"),
        limit: int = 10,
        market: str | None = Query(None, description="可选市场过滤 sh/sz/bj/hk/us"),
    ):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.search_symbols(pattern, limit=limit, market=market)}

    @app.get("/index_list", tags=["search"], summary="常用指数目录")
    def index_list():
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.index_list()}

    @app.get("/wencai", tags=["search"], summary="i问财自然语言选股（需服务端已配置 cookie）")
    def wencai(
        query: str = Query(..., description="自然语言条件，如 连板3板以上"),
        page: int = 1,
        limit: int = 50,
    ):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.wencai(query, page=page, limit=limit)}

    @app.get("/corporate_action/{symbol}", tags=["fundamental"], summary="权息资料（公司行为别名）")
    def corporate_action(symbol: str):
        # corporate_action 与 capital_changes 同通道；委托客户端同名/别名方法
        fn = getattr(real_client, "corporate_action", None) or getattr(
            real_client, "capital_changes", None
        )
        if fn is None or not callable(fn):
            raise HTTPException(status_code=404, detail="client has no method capital_changes")
        return {"data": fn(symbol)}

    # -- insight 组（3） -------------------------------------------------------- #
    @app.get("/stock_boards/{symbol}", tags=["search"], summary="个股所属板块（行业/概念/地域）")
    def stock_boards(symbol: str):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.stock_boards(symbol)}

    @app.get("/ipo", tags=["search"], summary="IPO 申购日历")
    def ipo(
        apply_date: str = Query("", description="申购日 YYYY-MM-DD；空串全量分页"),
        page: int = 1,
        size: int = 20,
    ):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.ipo_calendar(apply_date=apply_date, page=page, size=size)}

    @app.get("/big_order_flow/{symbol}", tags=["quotes"], summary="个股大单流向（五档资金分档）")
    def big_order_flow(symbol: str):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.big_order_flow(symbol)}

    @app.get("/stock_changes", tags=["quotes"], summary="盘中异动池（16 类异动，交易时段实时）")
    def stock_changes(
        types: str = Query("", description="逗号分隔异动类型（如 8201,8193）；空=全部"),
        page: int = 1,
        size: int = Query(50, le=500),
    ):
        from ..web.facade import WebQuoteSession

        try:
            parsed = tuple(int(t) for t in types.split(",") if t.strip())
        except ValueError as exc:
            # 解析异常面 500 → 400（V6/V2）
            raise HTTPException(status_code=400, detail=f"invalid types: {exc}") from exc
        return {"data": WebQuoteSession.stock_changes(parsed, page=page, size=size)}

    @app.get("/hot_rank", tags=["quotes"], summary="股吧个股人气榜")
    def hot_rank(page: int = 1, size: int = Query(100, le=100)):
        from ..web.facade import WebQuoteSession

        return {"data": WebQuoteSession.hot_rank(page=page, size=size)}

    @app.post("/query", tags=["system"], summary="统一响应形态调用（success/error/data/extra）")
    def query(payload: dict):
        """统一响应边界：仅白名单内的只读客户端方法可派发（V2），错误转 ``success=false``。"""
        from ..facade.response import err, ok

        method = str(payload.get("method", ""))
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise HTTPException(
                status_code=400,
                detail="payload.args must be a list and payload.kwargs must be an object",
            )
        if method not in SAFE_CLIENT_METHODS:
            raise HTTPException(status_code=404, detail=f"client method not allowed: {method!r}")
        try:
            return ok(_call(method, *args, **kwargs)).to_dict()
        except HTTPException:
            raise
        except TdxError as exc:  # 预期错误：保留 code + message
            return err(str(exc), code=exc.code).to_dict()
        except Exception:  # noqa: BLE001 —— 原生异常不外泄细节（V6）
            logger.error("query dispatch failed (method=%s)", method, exc_info=True)
            return err("internal error", code="E9001").to_dict()

    # -- goods 组（4） --------------------------------------------------------- #
    @app.get("/goods/bars", tags=["goods"], summary="商品 K 线")
    def goods_bars(
        code: str = Query(...), market: int = Query(...), period: str = "day", count: int = 100
    ):
        return {"data": _call("goods_bars", _mkt_sym(market, code), period=period, count=count)}

    @app.get("/goods/quote", tags=["goods"], summary="商品实时行情")
    def goods_quote(code: str = Query(...), market: int = Query(...)):
        return {"data": _call("goods_quote", _mkt_sym(market, code))}

    @app.post("/goods/quotes", tags=["goods"], summary="商品批量行情")
    def goods_quotes(payload: dict):
        codes = payload.get("codes") or []
        return {"data": [_call("goods_quote", c) for c in codes]}

    @app.get("/goods/summary", tags=["goods"], summary="商品市场摘要")
    def goods_summary(code: str = Query(...), market: int = Query(...)):
        rows = _call("goods_bars", _mkt_sym(market, code), period="day", count=1)
        return {"data": {"market": market, "count": len(rows) if rows else 0}}

    # -- extended 组（4） ------------------------------------------------------ #
    @app.get("/ex/bars", tags=["extended"], summary="扩展市场 K 线")
    def ex_bars(
        code: str = Query(...), market: int = Query(...), period: str = "day", count: int = 100
    ):
        return {"data": _call("ex_bars", _mkt_sym(market, code), period=period, count=count)}

    @app.get("/ex/quote", tags=["extended"], summary="扩展市场行情")
    def ex_quote(code: str = Query(...), market: int = Query(...)):
        return {"data": _call("ex_quote", _mkt_sym(market, code))}

    @app.get("/mac/quote", tags=["extended"], summary="MAC 行情")
    def mac_quote(code: str = Query(...), market: int = Query(...)):
        return {"data": _call("mac_quote", _mkt_sym(market, code))}

    @app.get("/download", tags=["extended"], summary="扩展市场文件下载")
    def download(symbol: str = Query(...), filename: str = Query(...)):
        # 深审 S#1：TdxClient 无 download（仅 F10Client.download(symbol, filename)
        # 且内部转发 file_download）——统一委托 file_download，两种客户端均可用。
        return {"data": _call("file_download", symbol, filename)}

    # -- system 组（4） --------------------------------------------------------- #
    @app.get("/system/health", tags=["system"], summary="健康检查")
    def health():
        return {"status": "ok", "client": type(real_client).__name__}

    @app.get("/system/metrics", tags=["system"], summary="Prometheus 指标")
    def metrics():
        from ..observability.metrics import metrics as obs_metrics

        return {"metrics": obs_metrics.render_prometheus()}

    @app.get("/system/specs", tags=["system"], summary="协议命令账本统计")
    def specs():
        from ..protocol.commands import COMMANDS
        from ..protocol.registry import PARSERS

        return {
            "commands": len(COMMANDS),
            "parsers": len(PARSERS),
        }

    @app.get("/system/version", tags=["system"], summary="版本信息")
    def version():
        return {"version": _version(), "service": "tstdx-http"}

    # -- tasks 组（6） ----------------------------------------------------------- #
    @app.post("/tasks", tags=["tasks"], summary="提交后台任务")
    def submit_task(payload: dict):
        method = payload.get("method")
        if not method:
            raise HTTPException(status_code=400, detail="payload.method required")
        method = str(method)
        if method not in SAFE_CLIENT_METHODS:
            # 已知但破坏性（如 close/open/request）→ 403；完全未知 → 400（V2）
            if _real_client_has_method(method) or callable(getattr(real_client, method, None)):
                raise HTTPException(
                    status_code=403, detail=f"client method not allowed: {method!r}"
                )
            raise HTTPException(status_code=400, detail=f"unknown client method {method!r}")
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise HTTPException(
                status_code=400,
                detail="payload.args must be a list and payload.kwargs must be an object",
            )
        fn = getattr(real_client, method, None)
        if fn is None or not callable(fn):
            raise HTTPException(status_code=400, detail=f"unknown client method {method!r}")
        try:
            task_id = tasks.submit(fn, *args, **kwargs)
        except TaskStoreFull as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"task_id": task_id, "status": "pending"}

    @app.get("/tasks", tags=["tasks"], summary="任务列表")
    def list_tasks():
        return {"data": tasks.list()}

    @app.get("/tasks/{task_id}", tags=["tasks"], summary="任务状态")
    def get_task(task_id: str):
        rec = tasks.get(task_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {"data": rec}

    @app.delete("/tasks/{task_id}", tags=["tasks"], summary="删除任务")
    def delete_task(task_id: str):
        if not tasks.delete(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        return {"deleted": task_id}

    @app.get("/tasks/{task_id}/result", tags=["tasks"], summary="任务结果")
    def task_result(task_id: str):
        rec = tasks.get(task_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {"status": rec["status"], "result": rec["result"], "error": rec["error"]}

    @app.post("/tasks/{task_id}/cancel", tags=["tasks"], summary="取消任务")
    def cancel_task(task_id: str):
        if not tasks.cancel(task_id):
            raise HTTPException(status_code=404, detail="task not cancellable")
        return {"cancelled": task_id}

    return app


def _version() -> str:
    from .. import __version__

    return __version__


def main() -> None:  # pragma: no cover —— CLI 入口
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn 未安装: pip install tstdx[server]") from exc
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)
