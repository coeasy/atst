# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""异步 TDX 在线客户端（REFACTOR_PLAN_v8 P5 拆出；v9 Q2 骨架化）。

包含 :class:`AsyncTdxClient` 与 AsyncGoods / AsyncExMarket / AsyncMac /
AsyncF10 四个异步镜像子客户端。公开导入路径不变：仍从 :mod:`atst.client`
导入（``client/__init__.py`` re-export）。

.. note:: 镜像方法的**方法体**自 v9 Q2 起统一上收至
    :class:`atst.client._mixin._ClientMixin` 模板；本文件仅保留 async 方法壳
    （公开签名、传输 seam ``async _req``）、生命周期与异步特有方法
    （``quotes_concurrent`` asyncio.Semaphore、``bestip`` executor 桥）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import atst.client as _client_pkg  # 包级符号经此转发（见 sync.py 说明）

from ..codec.framing import ResponseFrame
from ..errors import DataError
from ..protocol.commands import Family
from ..protocol.registry import ParseResult
from ._mixin import OutputFormat, _ClientMixin
from .core import (
    _emit,
    _guard_offline,
    _normalize_symbols,
    _require_int,
    _require_output_format,
)
from .sync import (
    _check_fixed_family_kwargs,
    _persist_standard,
    _probe_snapshot,
    _rank_entries,
    _require_unambiguous_pool_binding,
)


def dispatch(frame: ResponseFrame, **ctx: Any) -> ParseResult:
    """三级解析分派——经 :mod:`atst.client` 包级符号转发（见 sync.py）。"""
    return _client_pkg.dispatch(frame, **ctx)


# 异步客户端（镜像）
# --------------------------------------------------------------------------- #
class AsyncTdxClient(_ClientMixin):
    """异步 Tdx 在线客户端（基于 :class:`~atst.transport.async_.AsyncConnectionPool`）。

    方法签名与 :class:`TdxClient` 一一对应，仅多一个 ``await``。
    """

    def __init__(
        self,
        hosts: Sequence[Any] | None = None,
        *,
        family: str = Family.STANDARD,
        timeout: float = 5.0,
        max_retries: int = 3,
        pool: Any | None = None,
        **pool_kwargs: Any,
    ) -> None:
        # --- merged from _pool_binding_hardening: fail closed on ambiguous pool binding ---
        _require_unambiguous_pool_binding(
            pool=pool,
            family=family,
            hosts=hosts,
            max_retries=max_retries,
            pool_kwargs=pool_kwargs,
        )

        if pool is not None:
            self._pool = pool
            self._owns_pool = False
        else:
            from ..transport.async_ import AsyncConnectionPool
            from ..transport.hosts import resolve_hosts

            resolved = resolve_hosts(hosts, family=family)
            self._pool = AsyncConnectionPool(
                hosts=resolved,
                family=family,
                timeout=timeout,
                max_retries=max_retries,
                **pool_kwargs,
            )
            self._owns_pool = True
        self.family = family
        self.timeout = timeout
        # C3 镜像：显式声明（见同步版说明）
        self.last_errors: list[tuple[str, BaseException]] = []

    async def open(self, *, bestip: bool = False, **speedtest_kwargs: Any) -> AsyncTdxClient:
        """打开客户端；``bestip=True`` 时先做一轮运行时测速（镜像同步版）。

        这里不触发心跳：连接与回收者都由池自己武装（第一次握住真 socket 时起跑，
        见 :meth:`AsyncConnectionPool._get_conn_locked`），G47。
        """
        if bestip:
            await self.bestip(**speedtest_kwargs)
        return self

    async def bestip(
        self,
        *,
        timeout: float = 1.0,
        samples: int = 1,
        max_workers: int = 16,
        save_ranking: bool = True,
        keep_failures: bool = True,
    ) -> list[Any]:
        """异步运行时测速并热更新主站池（run_in_executor 不阻塞事件循环）。

        merged from _bestip_hardening.py：detached replace 快照 + 两阶段 commit。
        Executor 只做观测（speedtest），不持久化、不改 pool，避免取消后残留副作用。
        """
        import asyncio

        from ..transport.speedtest import speedtest

        hosts = _probe_snapshot(self._pool)
        if not hosts:
            return []
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: speedtest(
                hosts,
                family=self.family,
                timeout=timeout,
                samples=samples,
                max_workers=max_workers,
            ),
        )
        entries = _rank_entries(results, keep_failures=keep_failures)
        await self._pool.update_hosts(entries)
        _persist_standard(entries, family=self.family, save_ranking=save_ranking)
        return results

    async def close(self) -> None:
        if self._owns_pool:
            await self._pool.close()

    async def __aenter__(self) -> AsyncTdxClient:
        return await self.open()

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # -- last_errors 收尾钩子（异步镜像：无锁直赋，见 _ClientMixin） -------- #
    def _set_last_errors(self, errors: list[tuple[str, BaseException]]) -> None:
        self.last_errors = errors

    # -- K 线 / 分钟线 ------------------------------------------------------ #
    async def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: OutputFormat = "dict",
        strict: bool = False,
    ) -> Any:
        """异步 K 线 / 分钟线（参数与分页/截断语义见同步版 :meth:`TdxClient.bars`）。"""
        return await self._adrive(
            self._t_bars(
                symbol,
                period=period,
                count=count,
                start=start,
                market=market,
                index=index,
                as_format=as_format,
                strict=strict,
            )
        )

    async def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: OutputFormat = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> Any:
        """异步实时行情快照（参数与错误语义见同步版 :meth:`TdxClient.quotes`）。"""
        return await self._adrive(self._t_quotes(symbols, as_format=as_format, _collect=_collect))

    async def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """并发批量实时行情（asyncio.Semaphore 限并发；异步特有）。

        merged from _async_concurrency_hardening.py：先统一拿 canonical dict 行，
        最后只做一次 ``_emit`` 转换输出格式。per-symbol 错误收集隔离在 task 内部，
        主协程汇总后才触碰 ``last_errors``（C3 局部化镜像）。
        """
        import asyncio

        output_format = _require_output_format(as_format)
        syms = _normalize_symbols(symbols)
        worker_count = _require_int("workers", workers, minimum=1, maximum=64)
        if not syms:
            self.last_errors = []
            return _emit([], output_format)

        semaphore = asyncio.Semaphore(min(worker_count, len(syms)))

        async def _one(
            pair: tuple[int, str],
        ) -> tuple[int, dict[str, Any] | None, list[tuple[str, BaseException]]]:
            index, symbol = pair
            local_errors: list[tuple[str, BaseException]] = []
            async with semaphore:
                try:
                    rows = await self.quotes(
                        [symbol],
                        as_format="dict",
                        _collect=local_errors,
                    )
                    row = rows[0] if rows else None
                except Exception as exc:  # noqa: BLE001 - 单只失败隔离
                    local_errors.append((symbol, exc))
                    row = None
            return index, row, local_errors

        completed = await asyncio.gather(*(_one(pair) for pair in enumerate(syms)))
        out: list[dict[str, Any] | None] = [None] * len(syms)
        collected: list[tuple[str, BaseException]] = []
        for index, row, local_errors in completed:
            out[index] = row
            collected.extend(local_errors)

        self.last_errors = collected
        canonical = [row for row in out if row is not None]
        return _emit(canonical, output_format)

    # -- 元数据类 / 通用入口 / 更多标准命令（骨架模板壳） ------------------- #
    async def security_count(self, market: int | str = 0) -> int:
        """某市场的证券总数（命令 ``0x044E``）。"""
        return await self._adrive(self._t_security_count(market))

    async def capital_changes(self, symbol: str) -> list[Any]:
        """除权除息 / 股本变迁（命令 ``0x000F``）。"""
        return await self._adrive(self._t_capital_changes(symbol))

    async def finance_info(self, symbol: str) -> dict[str, Any]:
        """财务基础信息（命令 ``0x0010``；F1 语义化，对齐同步版）。"""
        return await self._adrive(self._t_finance_info(symbol))

    async def minute_today(self, symbol: str) -> list[Any]:
        """当日分时数据（命令 ``0x0537``）。"""
        return await self._adrive(self._t_minute_today(symbol))

    async def _req(self, cmd: int, body: bytes, *, timeout: float) -> ResponseFrame:
        """B5：传输统一入口（异步镜像）——offline 命令 fail-fast。"""
        _guard_offline(cmd)
        return await self._pool.request(cmd, body, timeout=timeout)

    async def request(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Mapping[str, Any] | None = None,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """异步通用命令入口（见同步版 :meth:`TdxClient.request`）。"""
        return await self._adrive(self._t_request(cmd, body, ctx=ctx, as_format=as_format))

    async def request_result(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Mapping[str, Any] | None = None,
    ) -> ParseResult:
        """异步版 :meth:`TdxClient.request_result`（返回完整解析元数据）。"""
        return await self._adrive(self._t_request_result(cmd, body, ctx=ctx))

    async def security_list(self, market: int | str = 0, start: int = 0) -> list[dict[str, Any]]:
        """代码表（命令 ``0x044D``，分页 1000/页）。"""
        return await self._adrive(self._t_security_list(market, start))

    async def export_security_list(
        self, market: int | str = 0, *, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        """全市场代码表导出（E3 异步镜像；截断告警语义与同步版一致）。"""
        return await self._adrive(self._t_export_security_list(market, max_pages=max_pages))

    async def minute_history(self, symbol: str, date: int) -> list[Any]:
        """指定日期历史分时（命令 ``0x0FB4``）。``date`` 为 YYYYMMDD 整数。"""
        return await self._adrive(self._t_minute_history(symbol, date))

    async def trade_today(
        self, symbol: str, start: int = 0, count: int = 0
    ) -> list[dict[str, Any]]:
        """当日逐笔成交（命令 ``0x0FC5``）。"""
        return await self._adrive(self._t_trade_today(symbol, start, count))

    async def block_quotes(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """板块行情（命令 ``0x07E5``）。block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数。"""
        return await self._adrive(self._t_block_quotes(block_type, start))

    async def file_download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int = 0,
        length: int = 0,
        max_packets: int = 500,
        strict: bool = False,
    ) -> bytes:
        """异步文件分块下载（全量循环/截断语义见同步版 :meth:`TdxClient.file_download`）。"""
        return await self._adrive(
            self._t_file_download(
                symbol,
                filename,
                offset=offset,
                length=length,
                max_packets=max_packets,
                strict=strict,
            )
        )

    async def _file_download_once(
        self, mkt: int, code: str, filename: str, offset: int, length: int
    ) -> list[dict[str, Any]]:
        """0x06B9 单次异步请求（v5 PG2，供全量模式循环复用）。"""
        return await self._adrive(self._t_file_download_once(mkt, code, filename, offset, length))

    # 兼容旧私有名（v5 PG2 拆出时的命名）
    _file_download_once_async = _file_download_once

    async def auction_snapshot(self, symbol: str) -> list[dict[str, Any]]:
        """集合竞价过程快照（命令 ``0x056A``）。"""
        return await self._adrive(self._t_auction_snapshot(symbol))

    async def volume_price_dist(self, symbol: str) -> list[dict[str, Any]]:
        """量价分布 / 筹码分布（命令 ``0x051A``）。"""
        return await self._adrive(self._t_volume_price_dist(symbol))

    async def quotes_snapshot(self, symbols: list[str]) -> Any:
        """异步批量行情快照（分片与回退语义见同步版 :meth:`TdxClient.quotes_snapshot`）。"""
        return await self._adrive(self._t_quotes_snapshot(symbols))

    async def snapshot(self, symbol: str, *, as_format: OutputFormat = "dict") -> Any:
        """实时行情 + 当日日线，合成一份快照（表驱动降级在 router 层完成）。"""
        return await self._adrive(self._t_snapshot(symbol, as_format=as_format))


# --------------------------------------------------------------------------- #
# 异步多协议族客户端别名
# --------------------------------------------------------------------------- #
class AsyncGoodsClient(AsyncTdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _check_fixed_family_kwargs("AsyncGoodsClient", Family.GOODS, kwargs)
        kwargs["family"] = Family.GOODS
        super().__init__(*args, **kwargs)

    async def goods_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """商品 K 线（命令 ``0x0202``，family=GOODS）。"""
        return await self._adrive(
            self._t_goods_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    async def goods_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """商品报价（命令 ``0x0203``，family=GOODS）。"""
        return await self._adrive(self._t_goods_quote(symbol, as_format))

    async def goods_count(self, market: int = 0) -> list[dict[str, Any]]:
        """商品数量（命令 ``0x0200``，family=GOODS；异步镜像见同步版）。"""
        return await self._adrive(self._t_goods_count(market))

    async def goods_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """商品列表（命令 ``0x0201``，family=GOODS；异步镜像见同步版）。"""
        return await self._adrive(self._t_goods_list(market, start))


class AsyncExMarketClient(AsyncTdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _check_fixed_family_kwargs("AsyncExMarketClient", Family.EXTENDED, kwargs)
        kwargs["family"] = Family.EXTENDED
        super().__init__(*args, **kwargs)

    async def ex_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """扩展市场 K 线（命令 ``0x0104``，family=EXTENDED）。"""
        return await self._adrive(
            self._t_ex_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    async def ex_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """扩展市场报价（命令 ``0x0105``，family=EXTENDED）。"""
        return await self._adrive(self._t_ex_quote(symbol, as_format))

    async def ex_market_count(self) -> list[dict[str, Any]]:
        """扩展市场数量（命令 ``0x0100``，family=EXTENDED；异步镜像见同步版）。"""
        return await self._adrive(self._t_ex_market_count())

    async def ex_market_list(self) -> list[dict[str, Any]]:
        """扩展市场列表（命令 ``0x0101``，family=EXTENDED；异步镜像见同步版）。"""
        return await self._adrive(self._t_ex_market_list())

    async def ex_instrument_count(self, market: int = 0) -> list[dict[str, Any]]:
        """扩展市场品种数量（命令 ``0x0102``，family=EXTENDED；异步镜像见同步版）。"""
        return await self._adrive(self._t_ex_instrument_count(market))

    async def ex_instrument_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """扩展市场品种列表（命令 ``0x0103``，family=EXTENDED；异步镜像见同步版）。"""
        return await self._adrive(self._t_ex_instrument_list(market, start))


class AsyncMacClient(AsyncTdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _check_fixed_family_kwargs("AsyncMacClient", Family.MAC, kwargs)
        kwargs["family"] = Family.MAC
        super().__init__(*args, **kwargs)

    async def mac_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """MAC 统一报价（命令 ``0x1301``，family=MAC）。"""
        return await self._adrive(self._t_mac_quote(symbol, as_format))

    async def block_list(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """板块列表（命令 ``0x120F``，family=MAC；异步镜像见同步版）。"""
        return await self._adrive(self._t_block_list(block_type, start))

    async def block_members(self, block_id: int, start: int = 0) -> list[dict[str, Any]]:
        """板块成分股（命令 ``0x1210``，family=MAC；异步镜像见同步版）。"""
        return await self._adrive(self._t_block_members(block_id, start))


class AsyncF10Client(AsyncTdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _check_fixed_family_kwargs("AsyncF10Client", Family.F10, kwargs)
        kwargs["family"] = Family.F10
        super().__init__(*args, **kwargs)

    async def download(
        self, symbol: str, filename: str, *, offset: int = 0, length: int = 0
    ) -> bytes:
        """下载 F10 文件片段（底层命令 0x06B9；空内容检测见同步版）。"""
        raw = await self.file_download(symbol, filename, offset=offset, length=length)
        if not raw:
            raise DataError(
                f"F10 文件 {filename!r} 下载结果为空（远端已停止 F10 内容分发，2026-09 实测）",
                context={"symbol": symbol, "filename": filename},
            )
        return raw

    async def catalog(self, symbol: str) -> list[dict[str, Any]]:
        """F10 栏目目录清单（命令 ``0x0001``，family=F10；异步镜像见同步版）。"""
        return await self._adrive(self._t_catalog(symbol))

    def parse_text(self, raw: bytes) -> list:
        from ..protocol.parsers.f10 import parse_f10_text

        return parse_f10_text(raw)
