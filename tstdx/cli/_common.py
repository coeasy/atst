# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""CLI 公共工具：表格输出与主站参数解析（零依赖，仅标准库）。"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

__all__ = [
    "_client_kwargs",
    "_fmt",
    "_pct",
    "_print_bars_table",
    "_print_rows",
    "_print_table",
    "_resolve_hosts",
    "_transport_kwargs",
    "_transport_timeout",
]


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    cols = [str(h) for h in headers]
    widths = [len(c) for c in cols]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def _fmt(v: Any, nd: int = 2) -> str:
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _pct(q: Mapping[str, Any]) -> float:
    """由 price / last_close 计算涨跌幅（Quote.to_dict 不含该字段）。"""
    pct = q.get("pct_change")
    if pct is not None:
        return float(pct)
    lc = q.get("last_close") or 0
    price = q.get("price") or 0
    if lc:
        return (price - lc) / lc * 100
    return 0.0


def _resolve_hosts(args: argparse.Namespace):
    from ..transport.hosts import parse_server

    hosts = getattr(args, "host", None)
    if hosts:
        return [parse_server(h) for h in hosts]
    return None


def _client_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Kernel path: forward only what the caller actually said.

    ``UnifiedRuntime`` is the single reader of the config surface, so an
    unset ``--host`` / ``--timeout`` must stay ``None`` here; substituting a
    CLI-side default would silently outrank ``tstdx.toml``.
    """
    return {"hosts": _resolve_hosts(args), "timeout": getattr(args, "timeout", None)}


def _transport_timeout(args: argparse.Namespace) -> float:
    """Timeout for a command that builds its client outside the kernel.

    ``[core] timeout`` is the documented default; an explicit ``--timeout``
    still wins. The kernel applies the same rule to the family clients it
    builds, so the CLI must not substitute its own literal default here.
    """
    from ..config import get_config

    timeout = getattr(args, "timeout", None)
    return float(timeout) if timeout is not None else get_config().core.timeout


def _transport_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Connection kwargs for raw **TDX-family** commands (``probe`` / ``blocks`` / …).

    These build a transport client outside the kernel, so nothing else applies
    the config surface for them; resolving it here is what keeps
    ``[hosts] servers`` and ``[core] timeout`` true on every command.
    """
    from ..config import get_config

    hosts = _resolve_hosts(args)
    return {
        "hosts": hosts if hosts is not None else (list(get_config().hosts.servers) or None),
        "timeout": _transport_timeout(args),
    }


# --------------------------------------------------------------------------- #
# 通用：按首行键生成表格
# --------------------------------------------------------------------------- #
def _print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(无数据)")
        return
    # 优先稳定顺序：常用键在前，其余按出现顺序
    preferred = [
        "code",
        "name",
        "market",
        "type",
        "time",
        "price",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "avg_price",
        "bid",
        "ask",
        "buyorsell",
        "num",
        "datetime",
    ]
    keys: list[str] = []
    for k in preferred:
        if k in rows[0]:
            keys.append(k)
    for k in rows[0]:
        if k not in keys:
            keys.append(k)
    table = [[r.get(k, "") for k in keys] for r in rows]
    _print_table(keys, table)


def _print_bars_table(data: list[dict[str, Any]]) -> None:
    """K 线表（Bar.to_dict 后的 dict 行）统一排版。"""
    rows = [
        [
            b.get("datetime", ""),
            _fmt(b.get("open", 0)),
            _fmt(b.get("high", 0)),
            _fmt(b.get("low", 0)),
            _fmt(b.get("close", 0)),
            int(b.get("volume", 0)),
            _fmt(b.get("amount", 0)),
        ]
        for b in data
    ]
    _print_table(["datetime", "open", "high", "low", "close", "volume", "amount"], rows)
