# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License
"""Web/门面子命令：adjusted-bars/all-market/minute-klines/baidu/fund/index/changes/hot。

REFACTOR_PLAN_v8 P3：自 ``tstdx/cli.py`` 拆出，行为不变。
"""

from __future__ import annotations

import argparse
import json
import sys

from ._common import _fmt, _print_bars_table, _print_rows, _print_table

__all__ = [
    "_cmd_adjusted_bars",
    "_cmd_all_market",
    "_cmd_baidu",
    "_cmd_changes",
    "_cmd_fund",
    "_cmd_hot",
    "_cmd_index",
    "_cmd_minute_klines",
    "_cmd_margin",
    "_cmd_sector_flow",
]


def _cmd_adjusted_bars(args: argparse.Namespace) -> int:
    """复权 K 线：``tstdx adjusted-bars <symbol> [--method qfq|hfq|fixed|none]``。

    门面 F1 链路：本地 vipdoc 日线 + 0x000F 除权除息事件 → 前/后/定点复权。
    """
    from ..domain.models import to_dicts
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            data = to_dicts(
                api.adjusted_bars(
                    args.symbol,
                    method=args.method,
                    period=args.period,
                    count=args.count,
                )
            )
    except Exception as exc:  # noqa: BLE001 - 门面（local+tdx+web）异常统一出口
        print(f"错误：复权 K 线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {args.period} [{args.method}] 共 {len(data)} 根")
    _print_bars_table(data)
    return 0


def _cmd_all_market(args: argparse.Namespace) -> int:
    """全市场行情摘要：``tstdx all-market [--node hs_a] [--source sina|tencent]``。"""
    from ..domain.models import to_dicts
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            data = to_dicts(
                api.all_market(
                    node=args.node,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                    source=args.source,
                )
            )
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：全市场行情获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.node}（{args.source}）共 {len(data)} 条")
    _print_rows(data)
    return 0


def _cmd_minute_klines(args: argparse.Namespace) -> int:
    """分钟 K 线：``tstdx minute-klines <symbol> [--period 5min] [--count 240]``。

    A 股走腾讯 mkline；港股/美股东财 push2his（Web 源）。
    """
    from ..domain.models import to_dicts
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            data = to_dicts(api.minute_klines(args.symbol, period=args.period, count=args.count))
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：分钟 K 线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {args.period} 共 {len(data)} 根")
    _print_bars_table(data)
    return 0


def _cmd_baidu(args: argparse.Namespace) -> int:
    """百度财经源：``tstdx baidu <symbol> [--kind kline|minute|ticks|quote]``。

    A 股专用（``sh600519`` / ``600519`` / ``sz301086``）；日/周/月 K 线含
    MA5/MA10/MA20 指标（挂 extra）。Web 源，无需 TDX 主站。
    """
    from ..domain.models import to_dicts
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            if args.kind == "minute":
                data = to_dicts(api.baidu_minute(args.symbol))
            elif args.kind == "ticks":
                data = to_dicts(api.baidu_ticks(args.symbol, limit=args.limit))
            elif args.kind == "quote":
                data = to_dicts([api.baidu_quote(args.symbol)])
            else:
                data = to_dicts(
                    api.baidu_kline(
                        args.symbol,
                        period=args.period,
                        count=args.count,
                        end_time=args.end_time,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：百度财经 {args.kind} 获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    if args.kind == "quote":
        _print_rows(data)
    elif args.kind == "kline":
        print(f"# {args.symbol} {args.period} 共 {len(data)} 根")
        _print_bars_table(data)
    else:
        print(f"# {args.symbol} {args.kind} 共 {len(data)} 条")
        _print_rows(data)
    return 0


def _cmd_fund(args: argparse.Namespace) -> int:
    """东财基金源：``tstdx fund nav|estimate|list``。

    * ``tstdx fund nav <code>`` —— 历史净值（--page-size/--page-index 翻页）；
    * ``tstdx fund estimate <code>`` —— 实时估值快照；
    * ``tstdx fund list`` —— 全量基金列表（约 1.2 万条，较大）。
    """
    from ..facade.api import UnifiedQuoteAPI

    if args.action in ("nav", "estimate") and not args.code:
        print(f"错误：fund {args.action} 需要 <code>（6 位基金代码）", file=sys.stderr)
        return 2
    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            if args.action == "nav":
                rows = api.fund_nav_history(
                    args.code, page_size=args.page_size, page_index=args.page_index
                )
            elif args.action == "estimate":
                rows = [api.fund_estimate(args.code)]
            else:
                rows = api.fund_list()
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：基金 {args.action} 获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# 基金 {args.action} 共 {len(rows)} 条")
    _print_rows(rows)
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    """东财指数成分股：``tstdx index constituents <code>``。

    * ``tstdx index constituents 000300`` —— 沪深300 成分股（--json 输出）。
    """
    from ..facade.api import UnifiedQuoteAPI

    if args.action == "constituents" and not args.code:
        print(
            "错误：index constituents 需要 <code>（指数代码，如 000300 / 000905 / 930050）",
            file=sys.stderr,
        )
        return 2
    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            rows = api.index_constituents(args.code)
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：指数成分获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# 指数 {args.code} 成分股共 {len(rows)} 条")
    _print_rows(rows)
    return 0


def _cmd_changes(args: argparse.Namespace) -> int:
    """盘中异动池（东财 push2ex getAllStockChanges；Web 源，无需主站）。"""
    from ..web.facade import WebQuoteSession

    try:
        # --types 解析进 try：ValueError 不再裸 traceback，按 usage → exit 2（V5）
        types = tuple(int(t) for t in str(args.types).split(",") if t.strip())
        rows = WebQuoteSession.stock_changes(types, page=args.page, size=args.size)
    except ValueError as exc:
        print(f"错误：--types 必须是逗号分隔整数 —— {exc}", file=sys.stderr)
        print("用法：tstdx changes --types 8201,8193 [--page N] [--size N]", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：盘中异动获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print("当前无异动数据（非交易时段为合法空状态）")
        return 0
    table = [
        [
            r["time"],
            r["code"],
            r["name"],
            str(r["change_type"]),
            r["change_name"],
            ",".join(str(m) for m in r["metrics"]),
        ]
        for r in rows
    ]
    _print_table(["time", "code", "name", "type", "change", "metrics"], table)
    return 0


def _cmd_hot(args: argparse.Namespace) -> int:
    """股吧个股人气榜（东财 emappdata stockrank；Web 源，无需主站）。"""
    from ..web.facade import WebQuoteSession

    try:
        rows = WebQuoteSession.hot_rank(page=args.page, size=args.size)
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：人气榜获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    table = [
        [str(r["rank"]), r["symbol"], r["code"], str(r["rank_change"]), str(r["his_rank_change"])]
        for r in rows
    ]
    _print_table(["rank", "symbol", "code", "chg", "his"], table)
    return 0


def _cmd_margin(args: argparse.Namespace) -> int:
    """个股融资融券明细：``tstdx margin <symbol> [--days N]``（东财 Web 源）。"""
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            rows = api.margin(args.symbol, days=args.days)
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：融资融券获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("无融资融券数据（可能非两融标的）")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} 两融明细最近 {len(rows)} 个交易日")
    table = [
        [
            r.get("date", ""),
            _fmt(r.get("rzye", 0)),
            _fmt(r.get("rzmre", 0)),
            _fmt(r.get("rzjme", 0)),
            _fmt(r.get("rqye", 0)),
            _fmt(r.get("rzrqye", 0)),
            _fmt(r.get("rzyezb", 0)),
        ]
        for r in rows
    ]
    _print_table(
        ["date", "rzye(元)", "rzmre(元)", "rzjme(元)", "rqye(元)", "rzrqye(元)", "rzyezb(%)"], table
    )
    return 0


def _cmd_sector_flow(args: argparse.Namespace) -> int:
    """板块资金流排行：``tstdx sector-flow [--board industry] [--sort main_net]``。"""
    from ..facade.api import UnifiedQuoteAPI

    try:
        with UnifiedQuoteAPI(timeout=args.timeout) as api:
            rows = api.sector_flow(args.board, sort=args.sort, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：板块资金流获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("无板块资金流数据")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.board} 板块资金流（按 {args.sort} 降序）")
    table = [
        [
            r.get("code", ""),
            r.get("name", ""),
            _fmt(r.get("change_pct", 0)) + "%",
            _fmt((r.get("main_net") or 0) / 1e8),
            _fmt(r.get("main_net_ratio", 0)) + "%",
            _fmt((r.get("super_large_net") or 0) / 1e8),
            _fmt((r.get("large_net") or 0) / 1e8),
        ]
        for r in rows
    ]
    _print_table(
        ["code", "name", "pct", "主力净流入(亿)", "主力占比%", "超大单(亿)", "大单(亿)"],
        table,
    )
    return 0
