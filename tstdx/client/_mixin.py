# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步 / 异步客户端共享方法骨架（模板 + trampoline SSOT）。"""

from __future__ import annotations

import logging
import struct
import warnings
from collections.abc import Mapping
from typing import Any

import tstdx.client as _client_pkg

from ..client_core import (
    _bars_body,
    _emit,
    _encode_gbk_field,
    _normalize_symbols,
    _quote_body,
    _require_bool,
    _require_int,
    _require_output_format,
    _require_yyyymmdd,
    _row_to_bar,
    _row_to_capital,
    _row_to_quote,
    _standard_market_id,
    period_to_category,
    split_symbol,
)
from ..domain.finance import FINANCE_INFO_FIELDS, map_finance_values
from ..domain.models import Bar, CapitalChange, Quote
from ..errors import ParseError, TdxError, TruncatedDataError
from ..protocol.commands import CMD
from ..protocol.parsers.std7709 import SecurityBarsParser, build_realtime_quote_body
from ..protocol.registry import TIER_L3

_QUOTES_SNAPSHOT_BATCH = 80
MAX_BARS_PER_REQUEST = SecurityBarsParser.MAX_BARS_PER_REQUEST
logger = logging.getLogger("tstdx.client")
OutputFormat = str
_TPL_WARN_STACKLEVEL = 4


def _op_req(cmd: int, body: bytes, **kw: Any) -> tuple[str, int, bytes, dict[str, Any]]:
    return ("req", cmd, body, kw)


def _op_call(name: str, *args: Any, **kwargs: Any) -> tuple[str, str, tuple, dict]:
    return ("call", name, args, kwargs)


class _ClientMixin:
    """同步 / 异步客户端共享骨架。"""

    def _drive(self, gen: Any) -> Any:
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = self._req(op[1], op[2], **op[3])  # type: ignore[attr-defined]
                    else:
                        _, name, args, kwargs = op
                        out = getattr(self, name)(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    op = gen.throw(exc)
                else:
                    op = gen.send(out)
        except StopIteration as stop:
            return stop.value

    async def _adrive(self, gen: Any) -> Any:
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = await self._req(op[1], op[2], **op[3])  # type: ignore[attr-defined]
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
        with self._errors_lock:  # type: ignore[attr-defined]
            self.last_errors = errors  # type: ignore[attr-defined]

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
        _require_output_format(as_format)
        _require_bool("index", index)
        strict_mode = _require_bool("strict", strict)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        if market is not None:
            mkt = _standard_market_id(market)

        remaining = _require_int("count", count, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        if offset + remaining > 0x10000:
            raise ParseError(
                f"start + count 超出 16-bit 分页地址空间: {offset} + {remaining}",
                context={"start": offset, "count": remaining},
            )

        bars: list[Bar] = []
        seen: set[str] = set()
        drifted = False
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
            raw_rows = result.rows
            if not raw_rows:
                break
            fresh = [row for row in raw_rows if str(row.get("datetime")) not in seen]
            seen.update(str(row.get("datetime")) for row in raw_rows)
            if not fresh:
                drifted = True
                break
            bars.extend(_row_to_bar(row) for row in fresh)
            if len(raw_rows) < page:
                break
            offset += len(raw_rows)
            remaining -= len(fresh)

        if len(bars) < count and drifted:
            msg = (
                f"bars({symbol!r}, period={period!r}, count={count}) 分页因盘中"
                f"锚点漂移提前终止：实取 {len(bars)} 根 < 请求 {count} 根"
            )
            if strict_mode:
                raise TruncatedDataError(
                    msg, context={"symbol": symbol, "returned": len(bars), "requested": count}
                )
            warnings.warn(msg, stacklevel=_TPL_WARN_STACKLEVEL)
        return _emit(bars, as_format)

    def _t_quotes(
        self,
        symbols: Any,
        *,
        as_format: OutputFormat,
        _collect: list[tuple[str, BaseException]] | None,
    ) -> Any:
        _require_output_format(as_format)
        normalized_symbols = _normalize_symbols(symbols)
        out: list[Quote] = []
        errors: list[tuple[str, BaseException]] = _collect if _collect is not None else []
        for sym in normalized_symbols:
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

    def _t_security_count(self, market: Any) -> int:
        market_id = _standard_market_id(market)
        body = struct.pack("<H", market_id) + b"\x00" * 4
        frame = yield _op_req(CMD["security_count"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, family=self.family)
        if not result.rows:
            raise ParseError("0x044E 无解析结果", context={"market": market_id})
        return int(result.rows[0].get("count", 0))

    def _t_capital_changes(self, symbol: str) -> list[CapitalChange]:
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        frame = yield _op_req(CMD["capital_changes"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family)
        return [_row_to_capital(row) for row in result.rows]

    def _t_finance_info(self, symbol: str) -> dict[str, Any]:
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

    def _t_minute_today(self, symbol: str) -> list[Any]:
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        frame = yield _op_req(CMD["minute_today"], body, timeout=self.timeout)
        result = _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family)
        return result.rows

    def _t_request(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Any,
        as_format: OutputFormat,
    ) -> Any:
        _require_output_format(as_format)
        result = yield _op_call("request_result", cmd, body, ctx=ctx)
        return _emit(result.rows, as_format)

    def _t_request_result(self, cmd: int, body: bytes, *, ctx: Any) -> Any:
        command = _require_int("cmd", cmd, minimum=0, maximum=0xFFFF)
        if not isinstance(body, bytes):
            raise ParseError(
                f"body 必须是 bytes，收到 {type(body).__name__}",
                context={"field": "body", "value_type": type(body).__name__},
            )
        if ctx is not None and not isinstance(ctx, Mapping):
            raise ParseError(
                f"ctx 必须是 Mapping 或 None，收到 {type(ctx).__name__}",
                context={"field": "ctx", "value_type": type(ctx).__name__},
            )
        frame = yield _op_req(command, body, timeout=self.timeout)
        return _client_pkg.dispatch(frame, family=self.family, **(dict(ctx) if ctx else {}))

    def _t_security_list(self, market: Any, start: int) -> Any:
        market_id = _standard_market_id(market)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request",
                CMD["security_list"],
                body,
                ctx={"market": market_id, "start": offset},
            )
        )

    def _t_export_security_list(self, market: Any, *, max_pages: int) -> Any:
        market_id = _standard_market_id(market)
        page_limit = _require_int("max_pages", max_pages, minimum=1)
        out: list[dict[str, Any]] = []
        start = 0
        truncated = True
        for _ in range(page_limit):
            rows = yield _op_call("security_list", market_id, start)
            if not rows:
                truncated = False
                break
            out.extend(rows)
            if len(rows) < 1000:
                truncated = False
                break
            start += len(rows)
            if start > 0xFFFF:
                break
        if truncated:
            warnings.warn(
                f"export_security_list(market={market_id}) 在 max_pages={page_limit} 页内"
                f"未取尽（已取 {len(out)} 条，最后一页仍为满页），结果可能截断",
                stacklevel=_TPL_WARN_STACKLEVEL,
            )
        return out

    def _t_minute_history(self, symbol: str, date: int) -> Any:
        mkt, code = split_symbol(symbol)
        day = _require_yyyymmdd("date", date)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<HI", mkt, day)
        frame = yield _op_req(CMD["minute_history"], body, timeout=self.timeout)
        return _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family).rows

    def _t_trade_today(self, symbol: str, start: int, count: int) -> Any:
        mkt, code = split_symbol(symbol)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        page_size = _require_int("count", count, minimum=0, maximum=0xFFFF)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack(
            "<HHH", mkt, offset, page_size
        )
        return (
            yield _op_call("request", CMD["trade_today"], body, ctx={"code": code, "market": mkt})
        )

    def _t_block_quotes(self, block_type: int, start: int) -> Any:
        kind = _require_int("block_type", block_type, minimum=0, maximum=3)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", kind, offset)
        return (
            yield _op_call(
                "request", CMD["block_quotes"], body, ctx={"block_type": kind, "start": offset}
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
        mkt, code = split_symbol(symbol)
        start_offset = _require_int("offset", offset, minimum=0, maximum=0xFFFFFFFF)
        requested_length = _require_int("length", length, minimum=0, maximum=0xFFFFFFFF)
        packet_limit = _require_int("max_packets", max_packets, minimum=1)
        strict_mode = _require_bool("strict", strict)
        _encode_gbk_field("filename", filename, max_bytes=80)

        if requested_length > 0:
            rows = yield _op_call(
                "_file_download_once",
                mkt,
                code,
                filename,
                start_offset,
                requested_length,
            )
            return b"".join(self._file_row_data(row) for row in rows)

        chunks = bytearray()
        total_len: int | None = None
        for _ in range(packet_limit):
            current_offset = start_offset + len(chunks)
            _require_int("offset", current_offset, minimum=0, maximum=0xFFFFFFFF)
            rows = yield _op_call(
                "_file_download_once", mkt, code, filename, current_offset, 0
            )
            if not rows:
                break
            raw_total = rows[0].get("total_len", total_len)
            if raw_total is not None:
                total_len = _require_int(
                    "total_len",
                    raw_total,
                    minimum=0,
                    maximum=0xFFFFFFFF,
                )
            data = self._file_row_data(rows[0])
            if not data:
                break
            chunks.extend(data)
            if total_len is not None and start_offset + len(chunks) >= total_len:
                break

        data = bytes(chunks)
        end_offset = start_offset + len(data)
        if total_len is not None and end_offset < total_len:
            msg = (
                f"file_download({symbol!r}, {filename!r}) 从 offset={start_offset} 累计"
                f" {len(data)} 字节，到达 {end_offset} < 服务端报告 total_len={total_len}，"
                "结果可能截断"
            )
            if strict_mode:
                raise TruncatedDataError(
                    msg,
                    context={
                        "symbol": symbol,
                        "filename": filename,
                        "offset": start_offset,
                        "returned": len(data),
                        "end_offset": end_offset,
                        "total_len": total_len,
                    },
                )
            warnings.warn(msg, stacklevel=_TPL_WARN_STACKLEVEL)
        return data

    @staticmethod
    def _file_row_data(row: Any) -> bytes:
        if not isinstance(row, dict):
            raise ParseError(
                f"file_download 响应行必须是 dict，收到 {type(row).__name__}",
                context={"row_type": type(row).__name__},
            )
        data = row.get("data", b"")
        if data is None:
            return b""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ParseError(
                f"file_download data 必须是 bytes-like，收到 {type(data).__name__}",
                context={"data_type": type(data).__name__},
            )
        return bytes(data)

    def _t_file_download_once(
        self, mkt: int, code: str, filename: str, offset: int, length: int
    ) -> Any:
        market_id = _standard_market_id(mkt)
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ParseError(
                f"file_download code 必须是 6 位 ASCII 数字: {code!r}",
                context={"code": code},
            )
        fn = _encode_gbk_field("filename", filename, max_bytes=80)
        start_offset = _require_int("offset", offset, minimum=0, maximum=0xFFFFFFFF)
        requested_length = _require_int("length", length, minimum=0, maximum=0xFFFFFFFF)
        body = (
            code.encode("ascii")
            + struct.pack("<H", market_id)
            + fn
            + struct.pack("<II", start_offset, requested_length)
        )
        return (yield _op_call("request", CMD["file_download"], body))

    def _t_auction_snapshot(self, symbol: str) -> Any:
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["auction_snapshot"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_volume_price_dist(self, symbol: str) -> Any:
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["volume_price_dist"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_quotes_snapshot(self, symbols: Any) -> Any:
        syms = _normalize_symbols(symbols)
        out: list[Any] = []
        errors: list[tuple[str, BaseException]] = []
        for index in range(0, len(syms), _QUOTES_SNAPSHOT_BATCH):
            chunk = syms[index : index + _QUOTES_SNAPSHOT_BATCH]
            try:
                body = struct.pack("<B", len(chunk))
                for sym in chunk:
                    mkt, code = split_symbol(sym)
                    body += struct.pack("<H", mkt) + code.encode("ascii")[:6].ljust(6, b"\x00")
                frame = yield _op_req(CMD["quotes_snapshot"], body, timeout=self.timeout)
                result = _client_pkg.dispatch(frame, family=self.family, price_scale=100)
            except TdxError as exc:
                logger.warning("quotes_snapshot 0x054C 批量失败，本片回退逐只 0x0530: %s", exc)
                errors.append(("0x054C", exc))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if not result.rows:
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if result.tier == TIER_L3:
                logger.warning("quotes_snapshot 0x054C 降级 L3 透传，本片回退逐只 0x0530")
                errors.append(("0x054C", TdxError("0x054C 响应仅 L3 透传")))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            out.extend(_row_to_quote(row) for row in result.rows)
        self._set_last_errors(errors)
        return _emit(out, "dict")

    def _t_snapshot(self, symbol: str, *, as_format: OutputFormat) -> Any:
        output_format = _require_output_format(as_format)
        if output_format == "tuple":
            raise ParseError(
                "snapshot 不支持 tuple 输出；可选 dict|dataframe",
                context={"as_format": as_format},
            )
        quote = yield _op_call("quotes", [symbol], as_format="dict")
        q = quote[0] if quote else None
        day = yield _op_call("bars", symbol, period="day", count=1, as_format="dict")
        d = day[0] if day else None
        snap = {"code": symbol, "quote": q, "prev_close": d}
        if output_format == "dataframe":
            from ..domain.models import to_dataframe

            return to_dataframe([snap])
        return snap

    def _t_goods_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        _require_output_format(as_format)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["goods_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _client_pkg.dispatch(frame, category=category, family=self.family)
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_goods_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(CMD["goods_quote"], _quote_body(code, mkt), timeout=self.timeout)
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_goods_count(self, market: int) -> Any:
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        body = struct.pack("<H", market_id)
        return (
            yield _op_call("request", CMD["goods_count"], body, ctx={"market": market_id})
        )

    def _t_goods_list(self, market: int, start: int) -> Any:
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request",
                CMD["goods_list"],
                body,
                ctx={"market": market_id, "start": offset},
            )
        )

    def _t_ex_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        _require_output_format(as_format)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _client_pkg.dispatch(frame, category=category, family=self.family)
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_ex_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_ex_market_count(self) -> Any:
        return (yield _op_call("request", CMD["ex_market_count"], b"\x00\x00", ctx={}))

    def _t_ex_market_list(self) -> Any:
        return (yield _op_call("request", CMD["ex_market_list"], b"\x00\x00", ctx={}))

    def _t_ex_instrument_count(self, market: int) -> Any:
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        body = struct.pack("<H", market_id)
        return (
            yield _op_call(
                "request", CMD["ex_instrument_count"], body, ctx={"market": market_id}
            )
        )

    def _t_ex_instrument_list(self, market: int, start: int) -> Any:
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request",
                CMD["ex_instrument_list"],
                body,
                ctx={"market": market_id, "start": offset},
            )
        )

    def _t_mac_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["mac_unified_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _client_pkg.dispatch(
            frame, code=code, market=mkt, price_scale=100, family=self.family
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_block_list(self, block_type: int, start: int) -> Any:
        kind = _require_int("block_type", block_type, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", kind, offset)
        return (
            yield _op_call(
                "request",
                CMD["mac_block_list"],
                body,
                ctx={"block_type": kind, "start": offset},
            )
        )

    def _t_block_members(self, block_id: int, start: int) -> Any:
        identifier = _require_int("block_id", block_id, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", identifier, offset)
        return (
            yield _op_call(
                "request",
                CMD["mac_block_members"],
                body,
                ctx={"block_id": identifier, "start": offset},
            )
        )

    def _t_catalog(self, symbol: str) -> Any:
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        return (
            yield _op_call("request", CMD["f10_catalog"], body, ctx={"code": code, "market": mkt})
        )