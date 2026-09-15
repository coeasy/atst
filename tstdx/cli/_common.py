# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""CLI 公共工具：表格输出与主站参数解析（零依赖，仅标准库）。"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

__all__ = ["_fmt", "_pct", "_print_bars_table", "_print_rows", "_print_table", "_resolve_hosts"]


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
