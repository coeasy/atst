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
    from typing_extensions import Self

import tstdx.client as _client_pkg

from ..client_core import (
    _emit,
    _guard_offline,
    _normalize_symbols,
    _require_int,
    _require_output_format,
)
from ..codec.framing import ResponseFrame
from ..errors import DataError
from ..protocol.commands import Family
from ..protocol.registry import ParseResult
from ._mixin import (  # noqa: F401
    _QUOTES_SNAPSHOT_BATCH,
    MAX_BARS_PER_REQUEST,
    OutputFormat,
    _ClientMixin,
)


def dispatch(frame: ResponseFrame, **ctx: Any) -> ParseResult:
    """三级解析分派——经 :mod:`tstdx.client` 包级符号转发。"""

    return _client_pkg.dispatch(frame, **ctx)


class TdxClient(_ClientMixin):
    """同步 TDX 在线客户端（基于 :class:`~tstdx.transport.pool.ConnectionPool`）。"""

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
        self.last_errors: list[tuple[str, BaseException]] = []
        self._errors_lock = threading.Lock()

    def open(self, *, bestip: bool = False, **speedtest_kwargs: Any) -> Self:
        if bestip:
            self.bestip(**speedtest_kwargs)
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
        """运行时测速并热更新主站池（对标 mootdx ``bestip=True``）。"""

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
        entries = rank_hosts(results if keep_failures else [result for result in results if result.ok])
        self._pool.update_hosts(entries)
        return results

    def close(self) -> None:
        if self._owns_pool:
            self._pool.close()

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

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
    ) -> Any: ...

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
    ) -> Any: ...

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        as_format: OutputFormat = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> Any:
        return self._drive(self._t_quotes(symbols, as_format=as_format, _collect=_collect))

    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: Literal["dict"] = "dict",
    ) -> list[dict[str, Any]]: ...

    @overload
    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: Literal["tuple"],
    ) -> list[tuple[Any, ...]]: ...

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
    ) -> Any: ...

    def quotes_concurrent(
        self,
        symbols: Sequence[str],
        *,
        workers: int = 8,
        as_format: OutputFormat = "dict",
    ) -> Any:
        """并发批量实时行情；worker 始终产 canonical dict，汇总后一次转换。

        这样 ``dataframe`` 不再被 worker 当作行列表做 ``rows[0]``，错误收集也
        由每个 worker 局部持有，再按输入顺序合并，不依赖 CPython list.append
        的实现级原子性。
        """

        from concurrent.futures import ThreadPoolExecutor

        output_format = _require_output_format(as_format)
        syms = _normalize_symbols(symbols)
        worker_count = _require_int("workers", workers, minimum=1, maximum=64)
        if not syms:
            with self._errors_lock:
                self.last_errors = []
            return _emit([], output_format)

        out: list[dict[str, Any] | None] = [None] * len(syms)
        collected: list[tuple[str, BaseException]] = []

        def _one(pair: tuple[int, str]) -> tuple[int, dict[str, Any] | None, list[tuple[str, BaseException]]]:
            index, symbol = pair
            local_errors: list[tuple[str, BaseException]] = []
            try:
                rows = self.quotes([symbol], as_format="dict", _collect=local_errors)
                row = rows[0] if rows else None
            except Exception as exc:  # noqa: BLE001 - 单只失败不影响其余
                local_errors.append((symbol, exc))
                row = None
            return index, row, local_errors

        with ThreadPoolExecutor(max_workers=min(worker_count, len(syms))) as executor:
            for index, quote, local_errors in executor.map(_one, enumerate(syms)):
                out[index] = quote
                collected.extend(local_errors)
        with self._errors_lock:
            self.last_errors = collected
        canonical = [quote for quote in out if quote is not None]
        return _emit(canonical, output_format)

    def security_count(self, market: int | str = 0) -> int:
        return self._drive(self._t_security_count(market))

    def capital_changes(self, symbol: str) -> list[Any]:
        return self._drive(self._t_capital_changes(symbol))

    def finance_info(self, symbol: str) -> dict[str, Any]:
        return self._drive(self._t_finance_info(symbol))

    def minute_today(self, symbol: str) -> list[Any]:
        return self._drive(self._t_minute_today(symbol))

    def _req(self, cmd: int, body: bytes, *, timeout: float) -> ResponseFrame:
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
        return self._drive(self._t_request(cmd, body, ctx=ctx, as_format=as_format))

    def request_result(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Mapping | None = None,
    ) -> ParseResult:
        return self._drive(self._t_request_result(cmd, body, ctx=ctx))

    def security_list(self, market: int | str = 0, start: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_security_list(market, start))

    def export_security_list(
        self, market: int | str = 0, *, max_pages: int = 100
    ) -> list[dict[str, Any]]:
        return self._drive(self._t_export_security_list(market, max_pages=max_pages))

    def minute_history(self, symbol: str, date: int) -> list[Any]:
        return self._drive(self._t_minute_history(symbol, date))

    def trade_today(self, symbol: str, start: int = 0, count: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_trade_today(symbol, start, count))

    def block_quotes(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
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
        return self._drive(self._t_file_download_once(mkt, code, filename, offset, length))

    def auction_snapshot(self, symbol: str) -> list[dict[str, Any]]:
        return self._drive(self._t_auction_snapshot(symbol))

    def volume_price_dist(self, symbol: str) -> list[dict[str, Any]]:
        return self._drive(self._t_volume_price_dist(symbol))

    def quotes_snapshot(self, symbols: list[str]) -> list[dict[str, Any]]:
        return self._drive(self._t_quotes_snapshot(symbols))

    def snapshot(self, symbol: str, *, as_format: OutputFormat = "dict") -> Any:
        return self._drive(self._t_snapshot(symbol, as_format=as_format))


class GoodsClient(TdxClient):
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
        return self._drive(
            self._t_goods_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    def goods_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        return self._drive(self._t_goods_quote(symbol, as_format))

    def goods_count(self, market: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_goods_count(market))

    def goods_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_goods_list(market, start))


class ExMarketClient(TdxClient):
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
        return self._drive(
            self._t_ex_bars(symbol, period=period, count=count, start=start, as_format=as_format)
        )

    def ex_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        return self._drive(self._t_ex_quote(symbol, as_format))

    def ex_market_count(self) -> list[dict[str, Any]]:
        return self._drive(self._t_ex_market_count())

    def ex_market_list(self) -> list[dict[str, Any]]:
        return self._drive(self._t_ex_market_list())

    def ex_instrument_count(self, market: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_ex_instrument_count(market))

    def ex_instrument_list(self, market: int = 0, start: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_ex_instrument_list(market, start))


class MacClient(TdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.MAC
        super().__init__(*args, **kwargs)

    def mac_quote(self, symbol: str, as_format: OutputFormat = "dict") -> Any:
        return self._drive(self._t_mac_quote(symbol, as_format))

    def block_list(self, block_type: int = 0, start: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_block_list(block_type, start))

    def block_members(self, block_id: int, start: int = 0) -> list[dict[str, Any]]:
        return self._drive(self._t_block_members(block_id, start))


class F10Client(TdxClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["family"] = Family.F10
        super().__init__(*args, **kwargs)

    def catalog(self, symbol: str) -> list[dict[str, Any]]:
        return self._drive(self._t_catalog(symbol))

    def download(self, symbol: str, filename: str, *, offset: int = 0, length: int = 0) -> bytes:
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
