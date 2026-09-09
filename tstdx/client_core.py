# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""客户端共享核心（B1 第一步）——同步 / 异步客户端共用的**纯协议构造**逻辑。

本模块只承载与传输无关的代码（请求体拼装、行 → 领域模型转换、输出格式化、
offline 命令守卫），**不含任何 I/O**；:class:`~tstdx.client.TdxClient` /
:class:`~tstdx.client.AsyncTdxClient` 仅各自保留传输 seam（``_req``）与
业务方法。后续步骤（B1 第二步）将把同名方法的重复方法体逐步合并到此处
共享，直至同步 / 异步只剩传输差异。
"""

from __future__ import annotations

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
    "_guard_offline",
    "_quote_body",
    "_row_to_bar",
    "_row_to_capital",
    "_row_to_quote",
    "period_to_category",
    "split_symbol",
]

OutputFormat = str  # "dict" | "tuple" | "dataframe"

#: 市场前缀 → 标准市场编号（与 :func:`split_code_market` 单一事实源一致：
#: 0=深 1=沪 2=北交所）。供 ``market="sh"`` 风格参数的命令使用。
#: v5 DC1：北交所从 0（与深市撞码）修正为独立市场编号 2（0x044E 实测：
#: market=2 → 379，北交所合理规模）。
_PREFIX_MARKET: dict[str, int] = {"sh": 1, "sz": 0, "bj": 2}


# --------------------------------------------------------------------------- #
# 符号解析（委托统一引擎 tstdx.domain.symbol）
# --------------------------------------------------------------------------- #
def split_symbol(symbol: str) -> tuple[int, str]:
    """把任意书写变种的证券代码拆成 ``(market, code)``。

    ``market`` 为**标准市场编号**（0=深 1=沪 2=北交所）。调用方必须再按
    具体命令族的已验证市场编码能力处理，不能把 2 静默钳制成 0/1。

    支持全部书写变种（大小写不敏感）：``sh600519`` / ``sh.600519`` /
    ``600519.sh`` / ``600519SH`` / ``600519``，
    统一经由 :mod:`tstdx.domain.symbol` 单一事实源处理。

    >>> split_symbol("sh600519")
    (1, '600519')
    >>> split_symbol("600519.SH")
    (1, '600519')
    >>> split_symbol("000001")   # 个股 000001 平安银行 → 深市
    (0, '000001')
    >>> split_symbol("sz000651")
    (0, '000651')
    """
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
    # 兼容别名
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
    """把人类可读的周期名映射到 K 线 ``category`` 整数。

    >>> period_to_category("day")
    4
    >>> period_to_category("15min")
    1
    """
    key = (period or "day").strip().lower()
    if key.isdigit():
        return int(key)  # 允许直接传 category 数字
    if key not in _PERIOD_TO_CATEGORY:
        raise ParseError(
            f"未知周期: {period!r}（可选 {sorted(_PERIOD_TO_CATEGORY)}）",
            context={"period": period},
        )
    return _PERIOD_TO_CATEGORY[key]


# --------------------------------------------------------------------------- #
# 行 → 领域模型
# --------------------------------------------------------------------------- #
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
            k: v
            for k, v in row.items()
            if k not in ("datetime", "open", "high", "low", "close", "volume", "amount")
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
    """除权除息解析行 → :class:`CapitalChange`（F1：统一走
    :func:`tstdx.domain.finance.to_capital_changes` 单一事实源）。"""
    return to_capital_changes([row])[0]


def _emit(items: Sequence[Any], as_format: OutputFormat):
    if as_format == "dataframe":
        from .domain.models import to_dataframe

        return to_dataframe(items)
    if as_format == "tuple":
        from .domain.models import to_tuples

        return to_tuples(items)
    # dict（默认）：调用 to_dict 保留 extra
    from .domain.models import to_dicts

    return to_dicts(items)


# --------------------------------------------------------------------------- #
# B5：offline 命令守卫
# --------------------------------------------------------------------------- #
#: 内部已有回退路径的 offline 命令豁免 fail-fast
#: （0x054C 批量失败时方法内部回退逐只 0x0530，见 :meth:`TdxClient.quotes_snapshot`）
_OFFLINE_FALLBACK_OK: frozenset[int] = frozenset({CMD["quotes_snapshot"]})


def _guard_offline(cmd: int) -> None:
    """B5：账本标记 offline 且无回退豁免的命令直接 fail-fast。

    7 条 ``STATUS_OFFLINE`` 命令多主站实测无响应；此前调用方仍要吃满
    「N 主站 × 3s 超时」重试链。现在请求发出前查账本，立即抛
    :class:`~tstdx.errors.CommandOffline`（含替代方案指引，<10ms）。
    误伤修复路径 = 更新账本 status（单一事实源）。
    """
    c = get_command(cmd)
    if c is not None and c.status == STATUS_OFFLINE and cmd not in _OFFLINE_FALLBACK_OK:
        raise CommandOffline(
            f"命令 0x{cmd:04X}（{c.name}）多主站实测无响应，已在客户端 fail-fast"
            "（不再走超时重试链）；请改用替代命令，或参考 PROTOCOL_SPEC 对应条目",
            context={"cmd": cmd, "name": c.name, "family": c.family},
        )


# --------------------------------------------------------------------------- #
# 请求体拼装（多协议族共用布局）
# --------------------------------------------------------------------------- #
def _bars_body(market: int, code: str, category: int, start: int, count: int) -> bytes:
    return struct.pack(
        "<H6sHHHHIIH",
        market,
        code.encode("ascii")[:6].ljust(6, b"\x00"),
        category,
        1,
        start,
        count,
        0,
        0,
        0,
    )


def _quote_body(code: str, market: int) -> bytes:
    """Build Goods/Extended/MAC quote body for a verified market identity.

    ``0x0530`` 的 0/1 market 字节反转语义已有实测证据。Goods ``0x0203``、
    Extended ``0x0105``、MAC ``0x1301`` 目前只沿用这两种已验证身份；北交所
    ``market=2`` 在这些命令族尚无 golden/真机编码证据，因此必须 fail closed，
    绝不能把 2 clamp 成 1/0 后伪装为其它市场。
    """

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
