# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""客户端共享核心：同步 / 异步客户端共用的纯协议构造与校验 SSOT。"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.finance import to_capital_changes
from ..domain.models import Bar, CapitalChange, Quote
from ..domain.symbol import to_tdx_market
from ..errors import CommandOffline, NotImplementedFeature, ParseError
from ..protocol.commands import CMD, STATUS_OFFLINE, get_command
from ..protocol.parsers.std7709 import KlineCategory

__all__ = [
    "OutputFormat",
    "_PREFIX_MARKET",
    "_bars_body",
    "_emit",
    "_encode_gbk_field",
    "_guard_offline",
    "_normalize_symbols",
    "_quote_body",
    "_require_bool",
    "_require_int",
    "_require_output_format",
    "_require_yyyymmdd",
    "_row_to_bar",
    "_row_to_capital",
    "_row_to_quote",
    "_standard_market_id",
    "period_to_category",
    "split_symbol",
]

OutputFormat = str
_PREFIX_MARKET: dict[str, int] = {"sh": 1, "sz": 0, "bj": 2}
#: 市场的数字写法，从 :data:`_PREFIX_MARKET` 的值派生：CLI、HTTP、MCP 三张面把这个旋钮声明成
#: 字符串（``--market`` 的缺省就是 ``"0"``），所以 ``"0"/"1"/"2"`` 是同一份契约的另一半写法，
#: 不是对脏输入的宽容。只认前缀会让那三张面连自己声明的缺省值都解不开——2026-09-22 盘中实测
#: ``tstdx security-count`` 与 ``GET /v13/security/count`` 都当场 E3040。
_MARKET_IDS_BY_TEXT: dict[str, int] = {str(value): value for value in _PREFIX_MARKET.values()}
_OUTPUT_FORMATS = frozenset({"dict", "tuple", "dataframe"})


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ParseError(
            f"{name} 必须是 bool，收到 {type(value).__name__}: {value!r}",
            context={"field": name, "value": value},
        )
    return value


def _require_int(
    name: str,
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(
            f"{name} 必须是整数，收到 {type(value).__name__}: {value!r}",
            context={"field": name, "value": value},
        )
    if minimum is not None and value < minimum:
        raise ParseError(
            f"{name}={value} 小于下限 {minimum}",
            context={"field": name, "value": value, "minimum": minimum},
        )
    if maximum is not None and value > maximum:
        raise ParseError(
            f"{name}={value} 超过上限 {maximum}",
            context={"field": name, "value": value, "maximum": maximum},
        )
    return value


def _require_output_format(value: Any) -> str:
    if not isinstance(value, str) or value not in _OUTPUT_FORMATS:
        raise ParseError(
            f"未知输出格式 {value!r}；可选 {sorted(_OUTPUT_FORMATS)}",
            context={"as_format": value, "allowed_formats": sorted(_OUTPUT_FORMATS)},
        )
    return value


def _normalize_symbols(symbols: Any, *, field: str = "symbols") -> list[str]:
    if isinstance(symbols, str):
        return [symbols]
    if isinstance(symbols, (bytes, bytearray, memoryview, Mapping)):
        raise ParseError(
            f"{field} 必须是字符串或字符串 Sequence，收到 {type(symbols).__name__}",
            context={"field": field, "value_type": type(symbols).__name__},
        )
    if not isinstance(symbols, Sequence):
        raise ParseError(
            f"{field} 必须是字符串或字符串 Sequence，收到 {type(symbols).__name__}",
            context={"field": field, "value_type": type(symbols).__name__},
        )
    items = list(symbols)
    for index, symbol in enumerate(items):
        if not isinstance(symbol, str):
            raise ParseError(
                f"{field}[{index}] 必须是字符串，收到 {type(symbol).__name__}",
                context={"field": field, "index": index, "value_type": type(symbol).__name__},
            )
    return items


def _require_yyyymmdd(name: str, value: Any) -> int:
    day = _require_int(name, value, minimum=19000101, maximum=21001231)
    text = f"{day:08d}"
    try:
        _dt.datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ParseError(
            f"{name} 不是合法 YYYYMMDD 日期: {day}",
            context={"field": name, "value": day},
            cause=exc,
        ) from exc
    return day


def _encode_gbk_field(name: str, value: Any, *, max_bytes: int) -> bytes:
    if not isinstance(value, str) or not value:
        raise ParseError(f"{name} 必须是非空字符串，收到 {value!r}", context={"field": name})
    if "\x00" in value:
        raise ParseError(f"{name} 不允许包含 NUL", context={"field": name})
    try:
        raw = value.encode("gbk", errors="strict")
    except UnicodeEncodeError as exc:
        raise ParseError(
            f"{name} 无法无损编码为 GBK: {value!r}",
            context={"field": name},
            cause=exc,
        ) from exc
    if len(raw) > max_bytes:
        raise ParseError(
            f"{name} GBK 长度 {len(raw)} 超过协议上限 {max_bytes} 字节",
            context={"field": name, "encoded_bytes": len(raw), "maximum": max_bytes},
        )
    return raw.ljust(max_bytes, b"\x00")


def _standard_market_id(market: Any) -> int:
    if isinstance(market, str):
        key = market.strip().lower()
        if key in _PREFIX_MARKET:
            return _PREFIX_MARKET[key]
        if key in _MARKET_IDS_BY_TEXT:
            return _MARKET_IDS_BY_TEXT[key]
        raise ParseError(
            f"未知标准市场 {market!r}；可选 {'/'.join(sorted(_PREFIX_MARKET))} "
            f"或 {'/'.join(sorted(_MARKET_IDS_BY_TEXT))}",
            context={"market": market},
        )
    return _require_int(
        "market",
        market,
        minimum=min(_PREFIX_MARKET.values()),
        maximum=max(_PREFIX_MARKET.values()),
    )


def split_symbol(symbol: str) -> tuple[int, str]:
    market, code = to_tdx_market(symbol)
    if market not in (0, 1):
        raise ParseError(
            f"symbol-based 7709 request 尚未验证 market={market}；当前只允许 SZ/SH",
            context={
                "symbol": symbol,
                "market": market,
                "verified_markets": [0, 1],
                "provider_switch_allowed": False,
            },
        )
    return market, code


_PERIOD_TO_CATEGORY: dict[str, int] = {
    "1min": KlineCategory.MIN_1,
    "min": KlineCategory.MIN_1,
    "1m": KlineCategory.MIN_1,
    "5min": KlineCategory.MIN_5,
    "15min": KlineCategory.MIN_15,
    "30min": KlineCategory.MIN_30,
    "60min": KlineCategory.HOUR_1,
    "1hour": KlineCategory.HOUR_1,
    "1h": KlineCategory.HOUR_1,
    "day": KlineCategory.DAY,
    "daily": KlineCategory.DAY,
    "week": KlineCategory.WEEK,
    "month": KlineCategory.MONTH,
    "season": KlineCategory.SEASON,
    "quarter": KlineCategory.SEASON,
    "year": KlineCategory.YEAR,
    "d": KlineCategory.DAY,
    "w": KlineCategory.WEEK,
    "m": KlineCategory.MONTH,
    "mo": KlineCategory.MONTH,
    "5m": KlineCategory.MIN_5,
    "15m": KlineCategory.MIN_15,
    "30m": KlineCategory.MIN_30,
    "60m": KlineCategory.HOUR_1,
}


def period_to_category(period: str) -> int:
    if not isinstance(period, str) or not period.strip():
        raise ParseError(f"period 必须是非空字符串，收到 {period!r}", context={"period": period})
    key = period.strip().lower()
    if key.isdigit():
        category = int(key)
        if category not in KlineCategory.NAMES:
            raise ParseError(
                f"未知 K 线 category={category}；可选 {sorted(KlineCategory.NAMES)}",
                context={"period": period, "category": category},
            )
        return category
    if key not in _PERIOD_TO_CATEGORY:
        raise ParseError(
            f"未知周期: {period!r}（可选 {sorted(_PERIOD_TO_CATEGORY)}）",
            context={"period": period},
        )
    return _PERIOD_TO_CATEGORY[key]


def _row_to_bar(row: Mapping[str, Any]) -> Bar:
    return Bar(
        datetime=row.get("datetime", ""),
        open=row.get("open", 0.0),
        high=row.get("high", 0.0),
        low=row.get("low", 0.0),
        close=row.get("close", 0.0),
        volume=int(row.get("volume", 0) or 0),
        amount=row.get("amount", 0.0),
        extra={
            key: value
            for key, value in row.items()
            if key not in ("datetime", "open", "high", "low", "close", "volume", "amount")
        },
    )


def _row_to_quote(row: Mapping[str, Any]) -> Quote:
    return Quote(
        code=str(row.get("code", "")),
        price=row.get("price", 0.0),
        last_close=row.get("last_close", 0.0),
        open=row.get("open", 0.0),
        high=row.get("high", 0.0),
        low=row.get("low", 0.0),
        volume=int(row.get("volume", 0) or 0),
        amount=row.get("amount", 0.0),
        bid=list(row.get("bid", []) or []),
        ask=list(row.get("ask", []) or []),
        extra=dict(row.get("extra", {}) or {}),
    )


def _row_to_capital(row: Mapping[str, Any]) -> CapitalChange:
    return to_capital_changes([row])[0]


def _emit(items: Sequence[Any], as_format: OutputFormat):
    output_format = _require_output_format(as_format)
    if output_format == "dataframe":
        from ..domain.models import to_dataframe

        return to_dataframe(items)
    if output_format == "tuple":
        from ..domain.models import to_tuples

        return to_tuples(items)
    from ..domain.models import to_dicts

    return to_dicts(items)


_OFFLINE_FALLBACK_OK: frozenset[int] = frozenset({CMD["quotes_snapshot"]})
_UNVERIFIED_STRUCTURED_BLOCK: frozenset[int] = frozenset(
    {
        CMD["minute_today"],
        CMD["trade_today"],
    }
)


def _guard_offline(cmd: int) -> None:
    command = get_command(cmd)
    name = command.name if command is not None else f"0x{cmd:04X}"
    summary = command.summary if command is not None else ""
    label = f"{name}：{summary}" if summary else name
    if cmd in _UNVERIFIED_STRUCTURED_BLOCK:
        raise NotImplementedFeature(
            f"命令 0x{cmd:04X}（{label}）当前 parser/request 仍为 inferred，"
            "在真机 golden 锁定前不通过结构化 TdxClient API 发包",
            context={
                "cmd": cmd,
                "name": name,
                "summary": summary,
                "raw_transport_available": True,
                "provider_switch_allowed": False,
            },
        )
    if command is not None and command.status == STATUS_OFFLINE and cmd not in _OFFLINE_FALLBACK_OK:
        raise CommandOffline(
            f"命令 0x{cmd:04X}（{label}）多主站实测无响应，已在客户端 fail-fast"
            "（不再走超时重试链）；请改用替代命令，或参考 PROTOCOL_SPEC 对应条目",
            context={
                "cmd": cmd,
                "name": command.name,
                "summary": command.summary,
                "family": command.family,
            },
        )


def _bars_body(market: int, code: str, category: int, start: int, count: int) -> bytes:
    del market, code, category, start, count
    raise NotImplementedFeature(
        "EXTENDED/GOODS bars request layout 尚未经过真机 golden 验证；已停止发送旧的标准 0x052D body",
        context={
            "commands": ["0x0104", "0x0202"],
            "expected_inferred_length": 12,
            "provider_switch_allowed": False,
        },
    )


def _quote_body(code: str, market: int) -> bytes:
    del code, market
    raise NotImplementedFeature(
        "EXTENDED/GOODS/MAC quote request layout 尚未经过真机 golden 验证；已停止发送推断 body",
        context={
            "commands": ["0x0105", "0x0203", "0x1301"],
            "provider_switch_allowed": False,
        },
    )
