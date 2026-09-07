# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步 TDX 在线客户端（REFACTOR_PLAN_v8 P5 拆出；v9 Q2 骨架化）。

包含 :class:`TdxClient` 与 Goods / ExMarket / Mac / F10 四个同步子客户端。
公开导入路径不变：仍从 :mod:`tstdx.client` 导入（``client/__init__.py`` re-export）。

.. note:: 镜像方法的**方法体**自 v9 Q2 起统一上收至
    :class:`tstdx.client._mixin._ClientMixin` 模板（模板 + trampoline，
    同步端经 :meth:`_ClientMixin._drive` 驱动）；本文件仅保留方法壳
    （公开签名 / overload / 传输 seam）、生命周期与同步特有方法
    （``quotes_concurrent`` 线程池、``bestip``）。``dispatch`` 经
    :mod:`tstdx.client` 包级符号转发（monkeypatch ``tstdx.client.dispatch``
    语义与拆分前单模块时代等价）。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    # 运行时零依赖（pyproject dependencies=[]）；Self 仅用于静态注解，
    # 在 `from __future__ import annotations` 下惰性不求值。
    from typing_extensions import Self

import tstdx.client as _client_pkg  # 包级符号经此转发（见 dispatch docstring）

from ..client_core import _guard_offline  # B1：共享核心
from ..codec.framing import ResponseFrame
from ..errors import DataError
from ..protocol.commands import Family
from ..protocol.registry import ParseResult

# v9 Q2：共享骨架（模板 + trampoline）与模块级常量（保持 re-export 兼容）
from ._mixin import (  # noqa: F401  (MAX_BARS_PER_REQUEST / _QUOTES_SNAPSHOT_BATCH / OutputFormat 经由本模块供 __init__ re-export)
    _QUOTES_SNAPSHOT_BATCH,
    MAX_BARS_PER_REQUEST,
    OutputFormat,
    _ClientMixin,
)


def dispatch(frame: ResponseFrame, **ctx: Any) -> ParseResult:
    """三级解析分派——经 :mod:`tstdx.client` 包级符号转发。

    拆分前本文件是单模块，测试可 ``monkeypatch.setattr(tstdx.client,
    "dispatch", spy)`` 拦截客户端内部调用；拆包后为保持该 monkeypatch
    语义等价，模板与壳层的 ``dispatch`` 一律经包属性在**调用时**解析。
    """
    return _client_pkg.dispatch(frame, **ctx)


# --------------------------------------------------------------------------- #
# 同步客户端
# --------------------------------------------------------------------------- #
class TdxClient(_ClientMixin):
    """同步 TDX 在线客户端（基于 :class:`~tstdx.transport.pool.ConnectionPool`）。

    Parameters
    ----------
    hosts:
        主站列表，形如 ``["host:port", ...]`` 或 :class:`~tstdx.transport.hosts.HostEntry`；
        ``None`` 时由 ``resolve_hosts`` 从内置候选池 + 用户配置合并。
    family:
        协议族，默认标准 7709。
    timeout, max_retries:
        单请求超时（秒）与失败重试次数（传给连接池）。
    pool:
        直接注入一个已建好的 :class:`~tstdx.transport.pool.ConnectionPool`，
        用于测试或共享连接。

    Examples
    --------
    >>> from tstdx import TdxClient
    >>> with TdxClient() as c:
    ...     bars = c.bars("sh600519", period="day", count=30)
    ...     print(bars[-1].close)
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
        if pool is not None:
            self._pool = pool
            self._owns_pool = False
        else:
            from ..transport.hosts import resolve_hosts
            from ..transport.pool import ConnectionPool

            resolved = resolve_hosts(hosts, family=family)
            self._pool = ConnectionPool(
                hosts=resolved,
                family=family,
                timeout=timeout,
                max_retries=max_retries,
                **pool_kwargs,
            )
            self._owns_pool = True
        self.family = family
        self.timeout = timeout
        # C3：显式声明（此前首访 AttributeError，cli getattr 兜底是症状）；
        # 并发路径（quotes_concurrent）结束时一次性赋值，需与直接调用互斥。
        self.last_errors: list[tuple[str, BaseException]] = []
        self._errors_lock = threading.Lock()

    # -- 生命周期 ----------------------------------------------------------- #
    def open(self, *, bestip: bool = False, **speedtest_kwargs: Any) -> Self:
        """打开客户端。

        ``bestip=True`` 时先做一轮运行时测速（:meth:`bestip`），把候选主站
        按当前实测 RTT 排序并热更新连接池，再返回。测速仅发心跳帧，安全无副作用。
        """
        if bestip:
            self.bestip(**speedtest_kwargs)
        # 连接池为惰性建立：首次 request 时才真正建连，无需显式 open。
        return self

    def bestip(
        self,
        *,
        timeout: float = 1.0,
        samples: int = 1,
        max_workers: int = 16,
        save_ranking: bool = True,
        keep_failures: bool = True,
    ) -> list[Any]:
        """运行时测速并热更新主站池（对标 mootdx ``bestip=True``）。

        对**当前池内全部候选主站**并发探测连接/往返耗时，按 RTT 升序重排
        主站优先级并立即生效（``update_hosts`` 热更新，不重启、不中断在飞连接）；
        同时可选把结果写入排名文件，下次启动直接复用。

        Returns
        -------
        按 RTT 升序的 ``list[ProbeResult]``（连接失败的排在最后）。
        """
        from ..transport.speedtest import rank_hosts, speedtest, speedtest_and_save

        hosts = list(self._pool.hosts)
        if not hosts:
            return []
        if save_ranking:
            results = speedtest_and_save(
                hosts,
                family=self.family,
                timeout=timeout,
                samples=samples,
                max_workers=max_workers,
                keep_failures=keep_failures,
            )
        else:
            results = speedtest(
                hosts,
                family=self.family,
                timeout=timeout,
                samples=samples,
                max_workers=max_workers,
            )
        entries = rank_hosts(results if keep_failures else [r for r in results if r.ok])
        self._pool.update_hosts(entries)
        return results

    def close(self) -> None:
        if self._owns_pool:
            self._pool.close()

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- K 线 / 分钟线 ------------------------------------------------------ #
    # overload：按 as_format 字面量收窄返回类型（L1b，修 cli.py 16 处
    # Bar|Any/Quote|Any 索引与 .get() 误报）
    @overload
    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: Literal["dict"] = "dict",
        strict: bool = False,
    ) -> list[dict[str, Any]]: ...

    @overload
    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: Literal["tuple"],
        strict: bool = False,
    ) -> list[tuple[Any, ...]]: ...

    @overload
    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: Literal["dataframe"],
        strict: bool = False,
    ) -> Any: ...

    @overload
    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        market: int | None = None,
        index: bool = False,
        as_format: str,
        strict: bool = False,
    ) -> Any:
        # 宽 fallback：运行期变量 as_format（OutputFormat = str）走此 stub
        ...

    def bars(
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
        """获取 K 线 / 分钟线（命令 ``0x052D``，**自动分页**；语义见骨架模板）。"""
        return self._drive(
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

    # -- 实时行情（逐只 0x0530） -------------------------------------------- #
    # overload：按 as_format 字面量收窄返回类型（L1b，同 bars）
    @overload
    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: Literal["dict"] = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> list[dict[str, Any]]: ...

    @overload
    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: Literal["tuple"],
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> list[tuple[Any, ...]]: ...

    @overload
    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: Literal["dataframe"],
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> Any: ...

    @overload
    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: str,
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> Any:
        # 宽 fallback：运行期变量 as_format（OutputFormat = str）走此 stub
        ...

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: OutputFormat = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> Any:
        """获取实时行情快照（命令 ``0x0530``，逐只请求；坏标的隔离见模板）。"""
        return self._drive(self._t_quotes(symbols, as_format=as_format, _collect=_collect))

    # overload：按 as_format 字面量收窄返回类型（L1b，同 quotes）
    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: Literal["dict"] = "dict",
    ) -> list[dict[str, Any] | None]: ...

    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: Literal["tuple"],
    ) -> list[tuple[Any, ...] | None]: ...

    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: Literal["dataframe"],
    ) -> Any: ...

    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: str,
    ) -> Any:
        # 宽 fallback：运行期变量 as_format（OutputFormat = str）走此 stub
        ...

    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: OutputFormat = "dict",
    ) -> list[Any]:
        """并发批量实时行情（E1：逐只 0x0530 的线程池版本；同步特有，不上收骨架）。

        与 :meth:`quotes_snapshot` 的区别：后者优先 0x054C 批量命令
        （实测已下线后退化为**串行**逐只）；本方法从一开始就按并发
        设计——把逐只请求分派到线程池，各线程经连接池的不同连接并行
        发送，N 只总延迟 ≈ ``ceil(N / workers) × 单次 RTT``。

        Returns
        -------
        成功行情列表（**保序**；失败或无解析结果的标的被跳过并记入
        :attr:`last_errors`）。
        """
        from concurrent.futures import ThreadPoolExecutor

        syms = [symbols] if isinstance(symbols, str) else list(symbols)
        out: list[Any] = [None] * len(syms)
        # C3：错误收集调用局部化——worker 内部经 _collect 汇入本列表，
        # 结束时一次性赋给 last_errors（不再被并发 quotes() 相互重置）。
        # list.append 在 CPython 下 GIL 原子，多 worker 追加安全。
        collected: list[tuple[str, BaseException]] = []

        def _one(pair: tuple[int, str]) -> tuple[int, Any]:
            i, sym = pair
            try:
                rows = self.quotes([sym], as_format=as_format, _collect=collected)
                return i, (rows[0] if rows else None)
            except Exception as exc:  # noqa: BLE001 - 单只失败不影响其余
                collected.append((sym, exc))
                return i, None

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(syms) or 1))) as ex:
            for i, q in ex.map(_one, enumerate(syms)):
                out[i] = q
        with self._errors_lock:
            self.last_errors = collected
        return [q for q in out if q is not None]

    # -- 元数据类 / 通用入口 / 更多标准命令（骨架模板壳） ------------------- #
    def security_count(self, market: int | str = 0) -> int:
        """某市场的证券总数（命令 ``0x044E``）。"""
        return self._drive(self._t_security_count(market))

    def capital_changes(self, symbol: str) -> list[Any]:
        """除权除息 / 股本变迁（命令 ``0x000F``）。"""
        return self._drive(self._t_capital_changes(symbol))

    def finance_info(self, symbol: str) -> dict[str, Any]:
        """财务基础信息（命令 ``0x0010``；F1 语义化字段）。"""
        return self._drive(self._t_finance_info(symbol))

    def minute_today(self, symbol: str) -> list[Any]:
        """当日分时数据（命令 ``0x0537``）。"""
        return self._drive(self._t_minute_today(symbol))

    # -- 通用命令入口 ------------------------------------------------------- #
    def _req(self, cmd: int, body: bytes, *, timeout: float) -> ResponseFrame:
        """B5：传输统一入口——先查账本 offline 状态 fail-fast，再进连接池。"""
        _guard_offline(cmd)
        return self._pool.request(cmd, body, timeout=timeout)

    def request(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Mapping | None = None,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """发送任意命令并返回解析后的行（供未在客户端封装的命令复用）。"""
        return self._drive(self._t_request(cmd, body, ctx=ctx, as_format=as_format))

    def request_result(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Mapping | None = None,
    ) -> ParseResult:
        """发送任意命令并返回完整 :class:`~tstdx.protocol.registry.ParseResult`。"""
        return self._drive(self._t_request_result(cmd, body, ctx=ctx))

    # -- 更多标准命令 ------------------------------------------------------- #
    def security_list(self, market: int | str = 0, start: int = 0) -> list[dict[str, Any]]:
        """代码表（命令 ``0x044D``，分页 1000/页）。"""
        return self._drive(self._t_security_list(market, start))

    def export_security_list(
        self, market: int | str = 0, *, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        """全市场代码表导出（E3：0x044D 分页遍历直至耗尽；截断告警）。"""
        return self._drive(self._t_export_security_list(market, max_pages=max_pages))

    def minute_history(self, symbol: str, date: int) -> list[Any]:
        """指定日期历史分时（命令 ``0x0FB4``）。``date`` 为 YYYYMMDD 整数。"""
        return self._drive(self._t_minute_history(symbol, date))

    def trade_today(self, symbol: str, start: int = 0, count: int = 0) -> list[dict[str, Any]]:
        """当日逐笔成交（命令 ``0x0FC5``）。"""
        return self._drive(self._t_trade_today(symbol, start, count))

    def block_quotes(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """板块行情（命令 ``0x07E5``）。block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数。"""
        return self._drive(self._t_block_quotes(block_type, start))

    def file_download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int = 0,
        length: int = 0,
        max_packets: int = 500,
        strict: bool = False,
    ) -> bytes:
        """服务器文件分块下载（命令 ``0x06B9``；全量/截断语义见骨架模板）。"""
        return self._drive(
            self._t_file_download(
                symbol,
                filename,
                offset=offset,
                length=length,
                max_packets=max_packets,
                strict=strict,
            )
        )

    def _file_download_once(
        self, mkt: int, code: str, filename: str, offset: int, length: int
    ) -> list[dict[str, Any]]:
        """0x06B9 单次请求（v5 PG2 拆出，供全量模式循环复用）。"""
        return self._drive(self._t_file_download_once(mkt, code, filename, offset, length))

    def auction_snapshot(self, symbol: str) -> list[dict[str, Any]]:
        """集合竞价过程快照（命令 ``0x056A``）。"""
        return self._drive(self._t_auction_snapshot(symbol))

    def volume_price_dist(self, symbol: str) -> list[dict[str, Any]]:
        """量价分布 / 筹码分布（命令 ``0x051A``）。"""
        return self._drive(self._t_volume_price_dist(symbol))

    def quotes_snapshot(self, symbols: list[str]) -> list[dict[str, Any]]:
        """批量行情快照（优先 0x054C，失败/空帧/L3 降级回退逐只 0x0530）。"""
        return self._drive(self._t_quotes_snapshot(symbols))

    # -- 便捷：单只完整快照 ------------------------------------------------ #
    def snapshot(self, symbol: str, *, as_format: OutputFormat = "dict") -> Any:
        """实时行情 + 当日日线，合成一份快照（表驱动降级在 router 层完成）。"""
        return self._drive(self._t_snapshot(symbol, as_format=as_format))


# --------------------------------------------------------------------------- #
# 多协议族客户端别名（复用既有请求体布局，映射到各家族命令号）
# --------------------------------------------------------------------------- #
class GoodsClient(TdxClient):
    """商品语义客户端（期货 / 期权 / 外汇，端口 7727，family=GOODS）。

    K 线走 0x0202、报价走 0x0203，布局与股票 0x052D/0x0530 同构，故复用
    既有请求体布局。其余命令走通用 :meth:`request`。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.GOODS
        super().__init__(*args, **kwargs)

    def goods_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """商品 K 线（命令 ``0x0202``，family=GOODS）。"""
        return self._drive(
            self._t_goods_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    def goods_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """商品报价（命令 ``0x0203``，family=GOODS）。"""
        return self._drive(self._t_goods_quote(symbol, as_format))

    def goods_count(self, market: int = 0) -> list[dict[str, Any]]:
        """商品数量（命令 ``0x0200``，family=GOODS）。返回 ``{"count"}`` 行。"""
        return self._drive(self._t_goods_count(market))

    def goods_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """商品列表（命令 ``0x0201``，family=GOODS）。返回 ``{"code", "name", "category"}`` 行。"""
        return self._drive(self._t_goods_list(market, start))


class ExMarketClient(TdxClient):
    """7727 扩展市场客户端（港股 / 美股 / 期货 / 外汇，family=EXTENDED）。

    K 线走 0x0104、报价走 0x0105。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.EXTENDED
        super().__init__(*args, **kwargs)

    def ex_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """扩展市场 K 线（命令 ``0x0104``，family=EXTENDED）。"""
        return self._drive(
            self._t_ex_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    def ex_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """扩展市场报价（命令 ``0x0105``，family=EXTENDED）。"""
        return self._drive(self._t_ex_quote(symbol, as_format))

    def ex_market_count(self) -> list[dict[str, Any]]:
        """扩展市场数量（命令 ``0x0100``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        return self._drive(self._t_ex_market_count())

    def ex_market_list(self) -> list[dict[str, Any]]:
        """扩展市场列表（命令 ``0x0101``，family=EXTENDED）。返回 ``{"market_id", "name"}`` 行。"""
        return self._drive(self._t_ex_market_list())

    def ex_instrument_count(self, market: int = 0) -> list[dict[str, Any]]:
        """扩展市场品种数量（命令 ``0x0102``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        return self._drive(self._t_ex_instrument_count(market))

    def ex_instrument_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """扩展市场品种列表（命令 ``0x0103``，family=EXTENDED）。返回 ``{"market", "code", "name"}`` 行。"""
        return self._drive(self._t_ex_instrument_list(market, start))


class MacClient(TdxClient):
    """MAC 专属客户端（PC 客户端分析数据，family=MAC）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.MAC
        super().__init__(*args, **kwargs)

    def mac_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        """MAC 统一报价（命令 ``0x1301``，family=MAC）。"""
        return self._drive(self._t_mac_quote(symbol, as_format))

    def block_list(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
        """板块列表（命令 ``0x120F``，family=MAC）。返回 ``{"name", "block_id"}`` 行。"""
        return self._drive(self._t_block_list(block_type, start))

    def block_members(self, block_id: int, start: int = 0) -> list[dict[str, Any]]:
        """板块成分股（命令 ``0x1210``，family=MAC）。返回 ``{"code"}`` 行。"""
        return self._drive(self._t_block_members(block_id, start))


class F10Client(TdxClient):
    """F10 资料网关客户端（文件型，family=F10）。

    用法：先 :meth:`catalog` 取栏目目录，再 :meth:`download` 下载某栏目 GBK 正文，
    最后用 :func:`tstdx.protocol.parsers.f10.parse_f10_text` 切分栏目。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.F10
        super().__init__(*args, **kwargs)

    def catalog(self, symbol: str) -> list[dict[str, Any]]:
        """F10 栏目目录清单（命令 ``0x0001``，family=F10；布局待真机定标）。"""
        return self._drive(self._t_catalog(symbol))

    def download(self, symbol: str, filename: str, *, offset: int = 0, length: int = 0) -> bytes:
        """下载 F10 文件片段（底层命令 0x06B9）。

        2026-09-06 实测：公开主站对 download 应答但返回**空字节**（F10 内容
        已停止分发）——空内容显式抛 :class:`~tstdx.errors.DataError`，不静默
        返回空文本。
        """
        raw = self.file_download(symbol, filename, offset=offset, length=length)
        if not raw:
            raise DataError(
                f"F10 文件 {filename!r} 下载结果为空（远端已停止 F10 内容分发，2026-09 实测）",
                context={"symbol": symbol, "filename": filename},
            )
        return raw

    def parse_text(self, raw: bytes) -> list:
        from ..protocol.parsers.f10 import parse_f10_text

        return parse_f10_text(raw)
