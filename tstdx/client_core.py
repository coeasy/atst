# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""客户端共享核心：同步 / 异步客户端共用的纯协议构造与校验 SSOT。"""

from __future__ import annotations

import datetime as _dt
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from .domain.finance import to_capital_changes
from .domain.models import Bar, CapitalChange, Quote
from .domain.symbol import to_tdx_market
from .errors import CommandOffline, ParseError
from .protocol.commands import CMD, STATUS_OFFLINE, get_command
from .protocol.parsers.std7709 import KlineCategory

__all__ = [
    "OutputFormat",
    "_PREFIX_MARKET",
    "_bars_body",
    "_emit",
    "_encode_gbk_field",
    "_guard_offline",
    "_quote_body",
    "_require_int",
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
_OUTPUT_FORMATS = frozenset({"dict", "tuple", "dataframe"})


def _require_int(
    name: str,
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Validate a protocol integer without bool/float/string coercion."""

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


def _require_yyyymmdd(name: str, value: Any) -> int:
    """Validate an actual Gregorian ``YYYYMMDD`` date, not just an integer range."""

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
    """Encode one fixed-width GBK protocol field without replacement or truncation."""

    if not isinstance(value, str) or not value:
        raise ParseError(
            f"{name} 必须是非空字符串，收到 {value!r}",
            context={"field": name},
        )
    if "\x00" in value:
        raise ParseError(
            f"{name} 不允许包含 NUL",
            context={"field": name},
        )
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
    """Parse exactly one standard TDX market identity: ``sz/sh/bj`` or ``0/1/2``."""

    if isinstance(market, str):
        key = market.strip().lower()
        if key not in _PREFIX_MARKET:
            raise ParseError(
                f"未知标准市场 {market!r}；可选 sz/sh/bj 或 0/1/2",
                context={"market": market},
            )
        return _PREFIX_MARKET[key]
    return _require_int("market", market, minimum=0, maximum=2)


def split_symbol(symbol: str) -> tuple[int, str]:
    """把证券代码拆成 TDX ``(market, code)``；HK/US 在 domain 层 fail closed。"""

    return to_tdx_market(symbol)


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
    """把人类可读周期映射到已声明的 K 线 category 0..11。"""

    if not isinstance(period, str) or not period.strip():
        raise ParseError(
            f"period 必须是非空字符串，收到 {period!r}",
            context={"period": period},
        )
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
    """Emit exactly one declared public output format; never silently coerce typos."""

    if as_format == "dataframe":
        from .domain.models import to_dataframe

        return to_dataframe(items)
    if as_format == "tuple":
        from .domain.models import to_tuples

        return to_tuples(items)
    if as_format == "dict":
        from .domain.models import to_dicts

        return to_dicts(items)
    raise ParseError(
        f"未知输出格式 {as_format!r}；可选 {sorted(_OUTPUT_FORMATS)}",
        context={"as_format": as_format, "allowed_formats": sorted(_OUTPUT_FORMATS)},
    )


_OFFLINE_FALLBACK_OK: frozenset[int] = frozenset({CMD["quotes_snapshot"]})


def _guard_offline(cmd: int) -> None:
    c = get_command(cmd)
    if c is not None and c.status == STATUS_OFFLINE and cmd not in _OFFLINE_FALLBACK_OK:
        raise CommandOffline(
            f"命令 0x{cmd:04X}（{c.name}）多主站实测无响应，已在客户端 fail-fast"
            "（不再走超时重试链）；请改用替代命令，或参考 PROTOCOL_SPEC 对应条目",
            context={"cmd": cmd, "name": c.name, "family": c.family},
        )


def _bars_body(market: int, code: str, category: int, start: int, count: int) -> bytes:
    market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
    category_id = _require_int("category", category, minimum=0, maximum=0xFFFF)
    offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
    page_size = _require_int("count", count, minimum=0, maximum=0xFFFF)
    return struct.pack(
        "<H6sHHHHIIH",
        market_id,
        code.encode("ascii")[:6].ljust(6, b"\x00"),
        category_id,
        1,
        offset,
        page_size,
        0,
        0,
        0,
    )


def _quote_body(code: str, market: int) -> bytes:
    """Build Goods/Extended/MAC quote body for a verified 0/1 market identity."""

    if isinstance(market, bool) or not isinstance(market, int):
        raise ParseError(
            f"quote market 必须是已验证整数 0|1，收到 {market!r}",
            context={"market": market, "verified_markets": [0, 1]},
        )
    if market not in (0, 1):
        raise ParseError(
            "Goods/Extended/MAC quote 的 market 反转编码目前仅验证 0/1；"
            f"拒绝未验证 market={market}",
            context={
                "market": market,
                "verified_markets": [0, 1],
                "provider_switch_allowed": False,
            },
        )
    return bytes([0x01, 1 - market]) + code.encode("ascii")[:6].ljust(6, b"\x00")
