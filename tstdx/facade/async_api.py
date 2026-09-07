# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""异步统一门面：以 ``asyncio.to_thread`` 桥接同步门面的核心方法。

设计要点
--------
* **方向**：本模块是「异步事件循环里跑同步阻塞调用」，用
  :func:`asyncio.to_thread`（线程池，默认 executor）实现，不阻塞事件循环。
* **实例委托**：每个 :class:`AsyncUnifiedQuoteAPI` 持有**自己的**
  :class:`~tstdx.facade.api.UnifiedQuoteAPI` 实例（惰性创建），桥接的是
  **绑定方法**——同步门面的方法均为实例方法（惰性客户端挂在其上），
  历史上以类对象直调会把首参误绑到 ``self`` 导致全部方法 TypeError。
* **语义对齐**：每个异步方法与 :class:`UnifiedQuoteAPI` 同名方法行为一致
  （含异常类型）；``aquery()`` 永不抛异常边界同样保留
  （返回失败 :class:`ApiResponse` 而非抛错）。
* **SourceUnavailable 语义继承（P14-E 契约）**：由于异步门面完全走
  ``asyncio.to_thread`` 桥接同步门面实例，同步门面在 auto/None 路由下
  的 ``CommandOffline`` / ``AllHostsUnreachable`` → :class:`SourceUnavailable`
  转换（P13-A）**自动继承**——异步层无需重复实现，也不应重复实现
  （避免双写漂移）。显式 ``route="tdx"`` 的透传行为同样继承。
  回归锁定：``tests/facade/test_w11_w12_w13.py::
  TestP14EAsyncFacadeSourceUnavailable``。
* **紧凑实现**：核心 10 方法覆盖最高频场景；长尾方法可直接
  ``await api.arun("method_name", *args, **kwargs)`` 泛化调用。
  **设计契约（REFACTOR_PLAN_v9 Q3）**：本类**有意不逐方法镜像**
  :class:`UnifiedQuoteAPI`——新增镜像前先修订
  ``tests/seams/test_contracts.py::test_seam_async_bridge_method_parity``
  的冻结核心集合与本 docstring。

Examples
--------
::

    api = AsyncUnifiedQuoteAPI()
    quotes = await api.quotes(["sh600519", "sz000001"])
    bars = await api.bars("sh600519", period="day", count=30)
    resp = await api.aquery("stock_changes", size=10)  # 永不抛异常

    async with AsyncUnifiedQuoteAPI() as api:  # 退出时关闭底层连接
        ...
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .api import UnifiedQuoteAPI
from .response import ApiResponse, from_result

__all__ = ["AsyncUnifiedQuoteAPI"]


class AsyncUnifiedQuoteAPI:
    """异步统一行情门面（:class:`UnifiedQuoteAPI` 的线程池桥接视图）。

    .. deprecated:: v7
        兼容门面：新代码请使用 :class:`~tstdx.client.AsyncTdxClient`
        或在线程池中运行 :class:`UnifiedQuoteAPI`；v8 评估删除。

    持有一个惰性创建的同步门面实例：首次调用时创建
    :class:`UnifiedQuoteAPI`（或其子类/替身，见 ``sync_api`` 参数），
    之后所有桥接走该实例的绑定方法。``close()`` 释放底层连接。
    """

    def __init__(self, *, sync_api: Any = None, **sync_kwargs: Any) -> None:
        """
        Parameters
        ----------
        sync_api:
            可选注入：任何提供同名方法的对象（如测试替身、预配置门面）。
        **sync_kwargs:
            透传给 :class:`UnifiedQuoteAPI` 构造器（route/hosts/timeout...）。
        """
        self._sync_kwargs = sync_kwargs
        self._sync: Any = sync_api

    # -- 底层同步门面（惰性） -------------------------------------------------- #
    @property
    def sync(self) -> Any:
        """承载桥接的 :class:`UnifiedQuoteAPI` 实例（惰性创建）。"""
        if self._sync is None:
            self._sync = UnifiedQuoteAPI(**self._sync_kwargs)
        return self._sync

    def close(self) -> None:
        """释放底层连接（同步门面及其 tdx/web 客户端）。"""
        if self._sync is not None:
            close = getattr(self._sync, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> AsyncUnifiedQuoteAPI:
        return self

    async def __aenter__(self) -> AsyncUnifiedQuoteAPI:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    async def __aexit__(self, *exc: Any) -> None:
        self.close()

    # -- 泛化通道 ------------------------------------------------------------ #
    async def arun(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """泛化调用：把任意 :class:`UnifiedQuoteAPI` 方法丢进线程池。

        以下划线开头的属性与未知方法名一律拒绝（防越权桥接内部方法）。

        Raises
        ------
        AttributeError
            ``method`` 不是 :class:`UnifiedQuoteAPI` 的公开方法。
        """
        if not method or method.startswith("_"):
            raise AttributeError(f"UnifiedQuoteAPI 没有公开方法 {method!r}")
        fn = getattr(self.sync, method, None)
        if fn is None or not callable(fn):
            raise AttributeError(f"UnifiedQuoteAPI 没有方法 {method!r}")
        return await asyncio.to_thread(fn, *args, **kwargs)

    # -- 行情与 K 线 ---------------------------------------------------------- #
    async def quotes(self, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        """实时行情快照（异步）。"""
        return await asyncio.to_thread(self.sync.quotes, list(symbols), **kwargs)

    async def bars(self, symbol: str, **kwargs: Any) -> list[Any]:
        """K 线（异步）。"""
        return await asyncio.to_thread(self.sync.bars, symbol, **kwargs)

    async def finance(self, symbol: str) -> dict[str, Any]:
        """财务基础信息（异步）。"""
        return await asyncio.to_thread(self.sync.finance, symbol)

    async def minute(self, symbol: str) -> list[Any]:
        """当日分时（异步）。"""
        return await asyncio.to_thread(self.sync.minute, symbol)

    async def capital_changes(self, symbol: str) -> list[Any]:
        """股本变迁 / 除权除息（异步）。"""
        return await asyncio.to_thread(self.sync.capital_changes, symbol)

    async def trades(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
        """当日逐笔成交（异步）。"""
        return await asyncio.to_thread(self.sync.trades, symbol, **kwargs)

    # -- Web 能力 -------------------------------------------------------------- #
    async def stock_changes(
        self, types: Sequence[int] = (), *, page: int = 1, size: int = 50
    ) -> list[dict[str, Any]]:
        """盘中异动池（异步）。"""
        return await asyncio.to_thread(self.sync.stock_changes, tuple(types), page=page, size=size)

    async def hot_rank(self, *, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """股吧人气榜（异步）。"""
        return await asyncio.to_thread(self.sync.hot_rank, page=page, size=size)

    async def wencai(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """i问财自然语言选股（异步；需调用方持有 cookie）。"""
        return await asyncio.to_thread(self.sync.wencai, query, **kwargs)

    async def search_symbols(self, pattern: str, **kwargs: Any) -> list[dict[str, str]]:
        """证券代码联想搜索（异步）。"""
        return await asyncio.to_thread(self.sync.search_symbols, pattern, **kwargs)

    # -- 永不抛异常边界 --------------------------------------------------------- #
    async def aquery(self, method: str, *args: Any, **kwargs: Any) -> ApiResponse:
        """``query()`` 的异步镜像：任何异常都折叠为失败 :class:`ApiResponse`。"""
        try:
            data = await self.arun(method, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 边界承诺：不外溢
            return from_result(exc)
        return from_result(data)
