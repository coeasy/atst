# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步 / 异步客户端共享方法骨架（REFACTOR_PLAN_v9 Q2）。

承载 ``TdxClient`` / ``AsyncTdxClient`` 及四个子客户端镜像方法对的**唯一**
方法体：协议体构造（复用 :mod:`tstdx.client_core` 纯函数）+ ``dispatch`` 解析
+ ``as_format`` 分派（``_emit``）+ ``last_errors`` 收尾。

异步差异的消除方式——**模板 + trampoline**：

* 每个镜像方法写成**同步生成器模板**（``_t_xxx``），凡原 ``await`` /
  ``self._req`` / ``self.<其他公开方法>`` 的位置改为
  ``yield _op_req(...)`` / ``yield _op_call(...)`` 挂起；
* 同步端 :meth:`_ClientMixin._drive` 逐条执行 op（直接调用传输 seam
  ``_req``），异步端 :meth:`_ClientMixin._adrive` 逐条 ``await``；
* op 执行抛出的异常经 ``gen.throw`` 在**原挂起点**重入模板，模板内的
  ``try/except TdxError``（坏标的隔离、0x054C 回退）语义逐字保留；
* ``warnings.warn`` 的 ``stacklevel`` 相应 +2（gen 帧 + drive 帧），
  告警归属仍为客户端调用方。

对外行为不变：公开方法壳留在 sync.py / async_.py（签名、overload、
docstring 摘要与迁移前一致）；``dispatch`` 经 :mod:`tstdx.client` 包级
符号在**调用时**解析（monkeypatch 语义不变，见 sync.py 说明）。
"""

from __future__ import annotations

import logging
import struct
import warnings
from typing import Any

import tstdx.client as _client_pkg  # 包级符号经此转发（见 sync.py 说明）

from ..client_core import (  # B1：共享核心（纯协议构造，无 I/O）
    _PREFIX_MARKET,
    _bars_body,
    _emit,
    _quote_body,
    _row_to_bar,
    _row_to_capital,
    _row_to_quote,
    period_to_category,
    split_symbol,
)
from ..domain.finance import FINANCE_INFO_FIELDS, map_finance_values
from ..domain.models import Bar, CapitalChange, Quote
from ..errors import (
    ParseError,
    TdxError,
    TruncatedDataError,
)
from ..protocol.commands import CMD, Family
from ..protocol.parsers.std7709 import (
    SecurityBarsParser,
    build_realtime_quote_body,
)
from ..protocol.registry import TIER_L3

#: ``quotes_snapshot`` 的 0x054C 单包标的数上限。历史实现一次打包全部标的，
#: ``>255`` 直接 struct.error 且绕过逐只回退设计；80 为保守分片值。
_QUOTES_SNAPSHOT_BATCH = 80

#: 0x052D 服务端单请求 K 线上限（v5 PG1：原 ``SecurityBarsParser`` 类常量
#: 是死代码，无任何调用方——现由 client 侧 ``bars()`` 真正消费，作为分页
#: 页大小。保持解析器常量为单一事实源，此处仅引用）。
MAX_BARS_PER_REQUEST = SecurityBarsParser.MAX_BARS_PER_REQUEST

logger = logging.getLogger("tstdx.client")

OutputFormat = str  # "dict" | "tuple" | "dataframe"

#: 模板内 ``warnings.warn`` 的 stacklevel：原实现（方法体直呼）为 2 =
#: 调用方；模板化后调用链多出 2 帧（生成器帧 + drive 帧），故 +2。
_TPL_WARN_STACKLEVEL = 4


def _op_req(cmd: int, body: bytes, **kw: Any) -> tuple[str, int, bytes, dict[str, Any]]:
    """op：发送单命令帧（sync ``self._req`` / async ``await self._req``）。"""
    return ("req", cmd, body, kw)


def _op_call(name: str, *args: Any, **kwargs: Any) -> tuple[str, str, tuple, dict]:
    """op：调用本客户端另一方法（sync 直呼 / async ``await``），保持
    monkeypatch 可见性与拆分前一致。"""
    return ("call", name, args, kwargs)


class _ClientMixin:
    """同步 / 异步客户端共享骨架（模板 + trampoline）。

    传输 seam 由子类实现：sync 为 ``_req(cmd, body, *, timeout)``，
    async 为同名 ``async _req``。``last_errors`` 收尾统一走
    :meth:`_set_last_errors` 钩子（sync 加锁、async 直赋——镜像既有差异）。
    """

    # The sync/async concrete clients provide these members.  They are
    # declared on the shared template so mypy can validate the trampoline
    # without weakening the concrete client contracts.
    timeout: float
    family: str
    _errors_lock: Any
    last_errors: list[tuple[str, BaseException]]
    _req: Any

    # ------------------------------------------------------------------ #
    # trampoline 驱动器
    # ------------------------------------------------------------------ #
    def _drive(self, gen: Any) -> Any:
        """同步驱动：执行模板挂起的 op，直到模板 return。"""
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = self._req(op[1], op[2], **op[3])
                    else:
                        _, name, args, kwargs = op
                        out = getattr(self, name)(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - 原样在挂起点重入模板
                    op = gen.throw(exc)
                else:
                    op = gen.send(out)
        except StopIteration as stop:
            return stop.value

    async def _adrive(self, gen: Any) -> Any:
        """异步驱动：逐条 ``await`` 模板挂起的 op（镜像 :meth:`_drive`）。"""
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = await self._req(op[1], op[2], **op[3])
                    else:
                        _, name, args, kwargs = op
                        out = await getattr(self, name)(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    op = gen.throw(exc)
                else:
                    op = gen.send(out)
        except StopIteration as stop:
            return stop.value

    def _set_last_errors(self, errors: list[tuple[str, BaseException]]) -> None:
        """``last_errors`` 收尾钩子（sync：与并发路径互斥加锁，C3）。"""
        with self._errors_lock:
            self.last_errors = errors

    # ------------------------------------------------------------------ #
    # K 线 / 分钟线
    # ------------------------------------------------------------------ #
    def _t_bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        start: int,
        market: int | None,
        index: bool,
        as_format: OutputFormat,
        strict: bool,
    ) -> Any:
        """K 线 / 分钟线（命令 ``0x052D``，自动分页 + 截断语义，见 sync 壳 docstring）。"""
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        if market is not None:
            mkt = market

        bars: list[Bar] = []
        seen: set[str] = set()
        remaining = max(0, int(count))
        offset = max(0, int(start))
        drifted = False  # 整页去重后零新增（锚点漂移），非正常历史耗尽

        while remaining > 0:
            page = min(remaining, MAX_BARS_PER_REQUEST)
            body = struct.pack(
                "<H6sHHHHIIH",
                mkt,
                code.encode("ascii")[:6].ljust(6, b"\x00"),
                category,
                1,
                offset,
                page,
                0,
                0,
                0,
            )
            frame = yield _op_req(CMD["security_bars"], body, timeout=self.timeout)
            result = _client_pkg.dispatch(frame, category=category, family=self.family, index=index)
            raw_rows = result.rows  # 原始 dict 行（去重以 datetime 字符串为键）
            if not raw_rows:
                break  # 空页：历史耗尽（正常终止）
            fresh = [r for r in raw_rows if str(r.get("datetime")) not in seen]
            seen.update(str(r.get("datetime")) for r in raw_rows)
            if not fresh:
                drifted = True
                break  # 整页重复：盘中锚点漂移，防死循环
            bars.extend(_row_to_bar(r) for r in fresh)
            if len(raw_rows) < page:
                break  # 短页：历史耗尽（正常终止）
            offset += len(raw_rows)
            remaining -= len(fresh)

        if len(bars) < count and drifted:
            msg = (
                f"bars({symbol!r}, period={period!r}, count={count}) 分页因盘中"
                f"锚点漂移提前终止：实取 {len(bars)} 根 < 请求 {count} 根"
            )
            if strict:
                raise TruncatedDataError(
                    msg, context={"symbol": symbol, "returned": len(bars), "requested": count}
                )
            warnings.warn(msg, stacklevel=_TPL_WARN_STACKLEVEL)
        return _emit(bars, as_format)

    # ------------------------------------------------------------------ #
    # 实时行情（逐只 0x0530）
    # ------------------------------------------------------------------ #
    def _t_quotes(
        self,
        symbols: Any,
        *,
        as_format: OutputFormat,
        _collect: list[tuple[str, BaseException]] | None,
    ) -> Any:
        """实时行情快照（命令 ``0x0530``，逐只请求；坏标的隔离见 sync 壳）。"""
        if isinstance(symbols, str):
            symbols = [symbols]
        out: list[Quote] = []
        errors: list[tuple[str, BaseException]] = _collect if _collect is not None else []
        for sym in symbols:
            # 坏标的隔离：符号解析/请求体构造失败只淘汰这一只（此前在
            # try 之外，一只坏标的会中断整批）
            try:
                mkt, code = split_symbol(sym)
                body = build_realtime_quote_body(code, mkt)
            except TdxError as exc:
                errors.append((sym, exc))
                continue
            try:
                frame = yield _op_req(CMD["realtime_quote"], body, timeout=self.timeout)
            except TdxError as exc:
                errors.append((sym, exc))
                continue
            result = _client_pkg.dispatch(
                frame,
                code=code,
                market=mkt,
                price_scale=100,
                family=self.family,
            )
            if not result.rows:
                errors.append((sym, ParseError(f"0x0530 无解析结果: {result.warnings}")))
                continue
            out.append(_row_to_quote(result.rows[0]))
        if _collect is None:
            self._set_last_errors(errors)
        return _emit(out, as_format)

    # ------------------------------------------------------------------ #
    # 元数据类
    # ------------------------------------------------------------------ #
    def _t_security_count(self, market: Any) -> int:  # type: ignore[misc]
        """某市场的证券总数（命令 ``0x044E``）。"""
        if isinstance(market, str):
            market = _PREFIX_MARKET.get(market.lower(), 0)
        body = struct.pack("<H", int(market)) + b"\x00" * 4
        frame = yield _op_req(CMD["security_count"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, family=self.family)
        if not result.rows:
            raise ParseError("0x044E 无解析结果", context={"market": market})
        return int(result.rows[0].get("count", 0))

    def _t_capital_changes(self, symbol: str) -> list[CapitalChange]:  # type: ignore[misc]
        """除权除息 / 股本变迁（命令 ``0x000F``）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        frame = yield _op_req(CMD["capital_changes"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family)
        return [_row_to_capital(r) for r in result.rows]

    def _t_finance_info(self, symbol: str) -> dict[str, Any]:  # type: ignore[misc]
        """财务基础信息（命令 ``0x0010``，F1 语义化字段）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        frame = yield _op_req(CMD["finance_info"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family)
        if not result.rows:
            return {}
        row = dict(result.rows[0])
        values = list(row.get("values", []) or [])
        out: dict[str, Any] = {"market": row.get("market", mkt), "code": row.get("code", code)}
        out.update(map_finance_values(values, FINANCE_INFO_FIELDS))
        out["values"] = values
        return out

    def _t_minute_today(self, symbol: str) -> list[Any]:  # type: ignore[misc]
        """当日分时数据（命令 ``0x0537``）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        frame = yield _op_req(CMD["minute_today"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family)
        return result.rows

    # ------------------------------------------------------------------ #
    # 通用命令入口
    # ------------------------------------------------------------------ #
    def _t_request(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Any,
        as_format: OutputFormat,
    ) -> Any:
        """任意命令 → 解析行（``as_format`` 自 v6 起真正生效）。"""
        result = yield _op_call("request_result", cmd, body, ctx=ctx)
        return _emit(result.rows, as_format)

    def _t_request_result(self, cmd: int, body: bytes, *, ctx: Any) -> Any:
        """任意命令 → 完整 :class:`~tstdx.protocol.registry.ParseResult`（v6 A2）。"""
        frame = yield _op_req(cmd, body, timeout=self.timeout)
        return _client_pkg.dispatch(frame, family=self.family, **(ctx or {}))

    # ------------------------------------------------------------------ #
    # 更多标准命令
    # ------------------------------------------------------------------ #
    def _t_security_list(self, market: Any, start: int) -> Any:
        """代码表（命令 ``0x044D``，分页 1000/页）。"""
        if isinstance(market, str):
            market = _PREFIX_MARKET.get(market.lower(), 0)
        body = struct.pack("<HH", int(market), int(start))
        return (
            yield _op_call(
                "request", CMD["security_list"], body, ctx={"market": market, "start": start}
            )
        )

    def _t_export_security_list(self, market: Any, *, max_pages: int) -> Any:
        """全市场代码表导出（E3：0x044D 分页遍历直至耗尽，截断告警）。"""
        if isinstance(market, str):
            market = _PREFIX_MARKET.get(market.lower(), 0)
        out: list[dict[str, Any]] = []
        start = 0
        truncated = True  # 空页/短页 break 时置 False；耗尽 max_pages 仍满页则为 True
        for _ in range(max(1, max_pages)):
            rows = yield _op_call("security_list", int(market), start)
            if not rows:
                truncated = False
                break
            out.extend(rows)
            if len(rows) < 1000:
                truncated = False
                break
            start += len(rows)
        if truncated:
            warnings.warn(
                f"export_security_list(market={market}) 在 max_pages={max_pages} 页内"
                f"未取尽（已取 {len(out)} 条，最后一页仍为满页），结果可能截断",
                stacklevel=_TPL_WARN_STACKLEVEL,
            )
        return out

    def _t_minute_history(self, symbol: str, date: int) -> Any:
        """指定日期历史分时（命令 ``0x0FB4``）。``date`` 为 YYYYMMDD 整数。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<HI", mkt, int(date))
        frame = yield _op_req(CMD["minute_history"], body, timeout=self.timeout)
        return _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family).rows

    def _t_trade_today(self, symbol: str, start: int, count: int) -> Any:
        """当日逐笔成交（命令 ``0x0FC5``）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack(
            "<HHH", mkt, int(start), int(count)
        )
        return (
            yield _op_call("request", CMD["trade_today"], body, ctx={"code": code, "market": mkt})
        )

    def _t_block_quotes(self, block_type: int, start: int) -> Any:
        """板块行情（命令 ``0x07E5``）。block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数。"""
        body = struct.pack("<HH", int(block_type), int(start))
        return (
            yield _op_call(
                "request", CMD["block_quotes"], body, ctx={"block_type": block_type, "start": start}
            )
        )

    def _t_file_download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int,
        length: int,
        max_packets: int,
        strict: bool,
    ) -> Any:
        """文件分块下载（命令 ``0x06B9``；全量/截断语义见 sync 壳 docstring）。"""
        mkt, code = split_symbol(symbol)
        if length > 0:
            rows = yield _op_call("_file_download_once", mkt, code, filename, offset, length)
            return b"".join(r.get("data", b"") or b"" for r in rows)

        chunks = bytearray()
        total_len: int | None = None
        for _ in range(max(1, int(max_packets))):
            rows = yield _op_call(
                "_file_download_once", mkt, code, filename, offset + len(chunks), 0
            )
            if not rows:
                break  # 服务端无更多数据
            total_len = rows[0].get("total_len", total_len)
            data = rows[0].get("data", b"") or b""
            if not data:
                break  # 空包：文件已取尽
            chunks.extend(data)
            if total_len is not None and len(chunks) >= total_len:
                break
        # for-else 不需要：max_packets 耗尽时由下方 total_len 校验兜底告警

        data = bytes(chunks)
        if total_len is not None and len(data) < total_len:
            msg = (
                f"file_download({symbol!r}, {filename!r}) 累计 {len(data)} 字节"
                f" < 服务端报告 total_len={total_len}，结果可能截断"
            )
            if strict:
                raise TruncatedDataError(
                    msg,
                    context={
                        "symbol": symbol,
                        "filename": filename,
                        "returned": len(data),
                        "total_len": total_len,
                    },
                )
            warnings.warn(msg, stacklevel=_TPL_WARN_STACKLEVEL)
        return data

    def _t_file_download_once(
        self, mkt: int, code: str, filename: str, offset: int, length: int
    ) -> Any:
        """0x06B9 单次请求（v5 PG2 拆出，供全量模式循环复用）。"""
        fn = filename.encode("gbk", errors="replace")[:80].ljust(80, b"\x00")
        body = (
            code.encode("ascii")[:6].ljust(6, b"\x00")
            + struct.pack("<H", int(mkt))
            + fn
            + struct.pack("<II", int(offset), int(length))
        )
        # 0x06B9 is shared by the standard transport and the F10 facade.  The
        # F10 family has no separate response parser for this command, so a
        # generic F10 request would silently discard the file chunk.  Parse it
        # through the canonical standard file-download parser for both clients.
        frame = yield _op_req(CMD["file_download"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, family=Family.STANDARD)
        return result.rows

    def _t_auction_snapshot(self, symbol: str) -> Any:
        """集合竞价过程快照（命令 ``0x056A``）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["auction_snapshot"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_volume_price_dist(self, symbol: str) -> Any:
        """量价分布 / 筹码分布（命令 ``0x051A``）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["volume_price_dist"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_quotes_snapshot(self, symbols: Any) -> Any:
        """批量行情快照（优先 0x054C，失败/空帧/L3 降级回退逐只 0x0530）。"""
        syms = [symbols] if isinstance(symbols, str) else list(symbols)
        # 混合容器：0x054C 批量路径收 Quote 对象，0x0530 回退路径收
        # as_format="dict" 的 dict（to_dicts 对两者都产 dict，运行时一致）。
        out: list[Any] = []
        errors: list[tuple[str, BaseException]] = []
        for i in range(0, len(syms), _QUOTES_SNAPSHOT_BATCH):
            chunk = syms[i : i + _QUOTES_SNAPSHOT_BATCH]
            try:
                body = struct.pack("<B", len(chunk))
                for sym in chunk:
                    mkt, code = split_symbol(sym)
                    body += struct.pack("<H", mkt) + code.encode("ascii")[:6].ljust(6, b"\x00")
                frame = yield _op_req(CMD["quotes_snapshot"], body, timeout=self.timeout)
                result = _client_pkg.dispatch(frame, family=self.family, price_scale=100)
            except TdxError as exc:
                # 批量失败（含主站不回 0x054C）：记日志 + 计数，本片回退逐只
                logger.warning("quotes_snapshot 0x054C 批量失败，本片回退逐只 0x0530: %s", exc)
                errors.append(("0x054C", exc))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if not result.rows:
                # 主站对 0x054C 回空帧（已下线的典型表现）→ 本片回退逐只
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if result.tier == TIER_L3:
                # 深审 M17：L3 = 原始透传（解析器不认识该响应）——把垃圾行
                # 经 _row_to_quote 强转成 Quote 是静默脏数据（字段全 None/0）。
                # 与批量失败同待遇：回退逐只 0x0530。
                logger.warning("quotes_snapshot 0x054C 降级 L3 透传，本片回退逐只 0x0530")
                errors.append(("0x054C", TdxError("0x054C 响应仅 L3 透传")))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            out.extend(_row_to_quote(r) for r in result.rows)
        self._set_last_errors(errors)
        return _emit(out, "dict")

    # ------------------------------------------------------------------ #
    # 便捷：单只完整快照
    # ------------------------------------------------------------------ #
    def _t_snapshot(self, symbol: str, *, as_format: OutputFormat) -> Any:
        """实时行情 + 当日日线，合成一份快照（表驱动降级在 router 层完成）。"""
        quote = yield _op_call("quotes", [symbol], as_format="dict")
        q = quote[0] if quote else None
        day = yield _op_call("bars", symbol, period="day", count=1, as_format="dict")
        d = day[0] if day else None
        snap = {
            "code": symbol,
            "quote": q,
            "prev_close": d,
        }
        if as_format == "dataframe":
            from ..domain.models import to_dataframe

            return to_dataframe([snap])
        return snap

    # ------------------------------------------------------------------ #
    # 多协议族子客户端模板（Goods / ExMarket / Mac / F10）
    # ------------------------------------------------------------------ #
    def _t_goods_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["goods_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _client_pkg.dispatch(frame, category=category, family=self.family)
        return _emit([_row_to_bar(r) for r in result.rows], as_format)

    def _t_goods_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(CMD["goods_quote"], _quote_body(code, mkt), timeout=self.timeout)
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(r) for r in result.rows], as_format)

    def _t_goods_count(self, market: int) -> Any:
        """商品数量（命令 ``0x0200``，family=GOODS）。返回 ``{"count"}`` 行。"""
        body = struct.pack("<H", int(market))
        return (yield _op_call("request", CMD["goods_count"], body, ctx={"market": market}))

    def _t_goods_list(self, market: int, start: int) -> Any:
        """商品列表（命令 ``0x0201``，family=GOODS）。返回 ``{"code", "name", "category"}`` 行。"""
        body = struct.pack("<HH", int(market), int(start))
        return (
            yield _op_call(
                "request", CMD["goods_list"], body, ctx={"market": market, "start": start}
            )
        )

    def _t_ex_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _client_pkg.dispatch(frame, category=category, family=self.family)
        return _emit([_row_to_bar(r) for r in result.rows], as_format)

    def _t_ex_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(r) for r in result.rows], as_format)

    def _t_ex_market_count(self) -> Any:
        """扩展市场数量（命令 ``0x0100``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        return (yield _op_call("request", CMD["ex_market_count"], b"\x00\x00", ctx={}))

    def _t_ex_market_list(self) -> Any:
        """扩展市场列表（命令 ``0x0101``，family=EXTENDED）。返回 ``{"market_id", "name"}`` 行。"""
        return (yield _op_call("request", CMD["ex_market_list"], b"\x00\x00", ctx={}))

    def _t_ex_instrument_count(self, market: int) -> Any:
        """扩展市场品种数量（命令 ``0x0102``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        body = struct.pack("<H", int(market))
        return (yield _op_call("request", CMD["ex_instrument_count"], body, ctx={"market": market}))

    def _t_ex_instrument_list(self, market: int, start: int) -> Any:
        """扩展市场品种列表（命令 ``0x0103``，family=EXTENDED）。返回 ``{"market", "code", "name"}`` 行。"""
        body = struct.pack("<HH", int(market), int(start))
        return (
            yield _op_call(
                "request", CMD["ex_instrument_list"], body, ctx={"market": market, "start": start}
            )
        )

    def _t_mac_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["mac_unified_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(r) for r in result.rows], as_format)

    def _t_block_list(self, block_type: int, start: int) -> Any:
        """板块列表（命令 ``0x120F``，family=MAC）。返回 ``{"name", "block_id"}`` 行。"""
        body = struct.pack("<HH", int(block_type), int(start))
        return (
            yield _op_call(
                "request",
                CMD["mac_block_list"],
                body,
                ctx={"block_type": block_type, "start": start},
            )
        )

    def _t_block_members(self, block_id: int, start: int) -> Any:
        """板块成分股（命令 ``0x1210``，family=MAC）。返回 ``{"code"}`` 行。"""
        body = struct.pack("<HH", int(block_id), int(start))
        return (
            yield _op_call(
                "request",
                CMD["mac_block_members"],
                body,
                ctx={"block_id": block_id, "start": start},
            )
        )

    def _t_catalog(self, symbol: str) -> Any:
        """F10 栏目目录清单（命令 ``0x0001``，family=F10；布局待真机定标）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        return (
            yield _op_call("request", CMD["f10_catalog"], body, ctx={"code": code, "market": mkt})
        )
