# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Argument parser for the v13 Client-first CLI.

v15 Phase 3 consolidation: all command handlers (v13 Client-backed ``cmd_*``
and legacy ``_cmd_*`` migrated from the former ``cmds_*.py`` modules) are
imported from :mod:`tstdx.cli.runtime_commands` and wired here. Business
routing is forbidden in the CLI; the CLI is a thin transport adapter.
"""

from __future__ import annotations

import argparse

from ..protocol.commands import Family
from .runtime_commands import (
    _cmd_adjusted_bars,
    _cmd_all_market,
    _cmd_baidu,
    _cmd_blocks,
    _cmd_changes,
    _cmd_f10,
    _cmd_feedback,
    _cmd_fund,
    _cmd_goods,
    _cmd_hosts_audit,
    _cmd_hosts_list,
    _cmd_hosts_scan,
    _cmd_hot,
    _cmd_index,
    _cmd_list,
    _cmd_margin,
    _cmd_minute_klines,
    _cmd_probe,
    _cmd_quotes_snapshot,
    _cmd_sector_flow,
    _cmd_serve,
    _cmd_server_test,
    cmd_bars,
    cmd_capabilities,
    cmd_minute,
    cmd_query,
    cmd_quotes,
    cmd_security_count,
    cmd_security_list,
    cmd_snapshot,
    cmd_stream,
    cmd_trades,
    cmd_version,
)

__all__ = ["build_parser"]

#: Canonical 5 protocol families the audit subcommand may target. Kept in sync
#: with ``tstdx.tools.host_audit._FAMILIES`` without importing the audit stack
#: at CLI parse time.
_AUDIT_FAMILIES = (
    Family.STANDARD,
    Family.EXTENDED,
    Family.MAC,
    Family.GOODS,
    Family.F10,
)


def _provider_args(parser: argparse.ArgumentParser, *, fallback: bool = False) -> None:
    parser.add_argument("--provider", default=None if fallback else "tdx")
    if fallback:
        parser.add_argument(
            "--fallback", help="explicit comma-separated Provider order, e.g. tdx,tencent,sina"
        )
    # 兼容契约：行情类命令保留 --host（单/多主站选择），v13 Client 不消费但解析透传。
    parser.add_argument(
        "--host", action="append", default=[], help="explicit host:port overrides (repeatable)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tstdx", description="tstdx v13 Provider-first market-data client"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version", help="show package version")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("capabilities", help="list migrated v13 business capabilities")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("query", help="execute any migrated v13 capability")
    p.add_argument("capability")
    p.add_argument("--provider")
    p.add_argument("--channel")
    p.add_argument(
        "--currentness", default="business", choices=("auto", "live", "historical", "business")
    )
    p.add_argument("--max-age", type=float)
    p.add_argument(
        "--args", dest="args_json", default="[]", help="JSON array of positional arguments"
    )
    p.add_argument(
        "--kwargs", dest="kwargs_json", default="{}", help="JSON object of keyword arguments"
    )
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("quotes", help="query live quotes")
    p.add_argument("symbols", nargs="+")
    _provider_args(p, fallback=True)
    p.add_argument("--max-age", type=float)
    p.set_defaults(func=cmd_quotes)

    p = sub.add_parser("bars", help="query historical bars")
    p.add_argument("symbol")
    _provider_args(p, fallback=True)
    p.add_argument("--period", default="day")
    p.add_argument("--count", type=int, default=320)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--adjustment", default="")
    p.add_argument("--max-age", type=float)
    p.set_defaults(func=cmd_bars)

    p = sub.add_parser("snapshot", help="query canonical market snapshot")
    p.add_argument("symbol")
    _provider_args(p)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("minute", help="query intraday minute data")
    p.add_argument("symbol")
    _provider_args(p)
    p.set_defaults(func=cmd_minute)

    p = sub.add_parser("trades", help="query intraday trades/ticks")
    p.add_argument("symbol")
    _provider_args(p)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--count", type=int, default=0)
    p.set_defaults(func=cmd_trades)

    p = sub.add_parser("security-count", help="query security count")
    _provider_args(p)
    p.add_argument("--market", default="0")
    p.set_defaults(func=cmd_security_count)

    p = sub.add_parser("security-list", help="query security catalog")
    _provider_args(p)
    p.add_argument("--market", default="0")
    p.add_argument("--start", type=int, default=0)
    p.set_defaults(func=cmd_security_list)

    p = sub.add_parser("stream", help="stream live quotes using Stateful streaming")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--provider", default="tdx")
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--diff-only", dest="diff", action="store_true")
    p.add_argument("--max-queue", type=int, default=1024)
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_stream)

    # --- hosts / transport (migrated from cmds_hosts.py) ---

    hosts_p = sub.add_parser("hosts", help="主站管理（巡检/测速/列表）")
    hosts_p.add_argument("--timeout", type=float, default=5.0)
    hosts_p.add_argument("--hosts-file", default=None)
    hosts_p.add_argument("--quiet", action="store_true")
    hosts_p.add_argument("--strict", action="store_true")
    hosts_sub = hosts_p.add_subparsers(dest="hosts_command", required=True)

    audit_p = hosts_sub.add_parser("audit", help="5 族主站巡检")
    audit_p.add_argument(
        "--family",
        action="append",
        default=[],
        choices=_AUDIT_FAMILIES,
        help="要巡检的协议族；可多次指定；默认全部 5 族",
    )
    audit_p.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    audit_p.add_argument("--samples", type=int, default=3)
    audit_p.add_argument("--workers", type=int, default=16)
    audit_p.add_argument("--report", default="./host_audit_report.json")
    audit_p.add_argument("--markdown", default=None)
    audit_p.add_argument("--ranking-file", default="~/.tstdx/server_ranking.json")
    audit_p.add_argument("--no-save-ranking", action="store_true")
    # ``--strict`` / ``--quiet`` are also accepted at the ``hosts`` group level.
    # Re-declaring them on ``audit`` keeps the natural ``hosts audit --strict``
    # spelling working (argparse only parses group-level options before the
    # subcommand token).
    audit_p.add_argument("--strict", action="store_true")
    audit_p.add_argument("--quiet", action="store_true")
    audit_p.set_defaults(func=_cmd_hosts_audit)

    # ``hosts list`` / ``hosts scan`` are first-class operators of the same
    # subcommand group; dropping them from the parser (while their handlers
    # stayed in ``runtime_commands``) silently removed a supported surface.
    list_p = hosts_sub.add_parser("list", help="查看当前生效的主站池")
    list_p.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    list_p.set_defaults(func=_cmd_hosts_list)

    scan_p = hosts_sub.add_parser("scan", help="并发测速候选池并写入排名文件")
    scan_p.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    scan_p.set_defaults(func=_cmd_hosts_scan)

    st_p = sub.add_parser("server-test", help="主站连通性测速")
    st_p.add_argument("--timeout", type=float, default=5.0)
    st_p.set_defaults(func=_cmd_server_test)

    serve_p = sub.add_parser("serve", help="启动 HTTP 行情网关")
    serve_p.add_argument("--port", type=int, default=None)
    serve_p.add_argument("--bind", default=None)
    serve_p.set_defaults(func=_cmd_serve)

    # --- feedback (migrated from cmds_hosts.py) ---

    fb_p = sub.add_parser("feedback", help="反馈上报")
    fb_sub = fb_p.add_subparsers(dest="feedback_command", required=True)
    fb_submit = fb_sub.add_parser("submit", help="提交反馈")
    fb_submit.add_argument("--message", required=True)
    fb_submit.add_argument("--endpoint", default=None)
    fb_submit.add_argument("--store-dir", default=None)
    fb_stats = fb_sub.add_parser("stats", help="本地统计")
    fb_stats.add_argument("--json", action="store_true")
    fb_p.set_defaults(func=_cmd_feedback)

    # --- probe (migrated from cmds_hosts.py) ---

    probe_p = sub.add_parser("probe", help="未知命令主动探测")
    probe_p.add_argument("cmd")
    probe_p.add_argument("--market", type=int, default=0)
    probe_p.add_argument("--code", default="")
    probe_p.add_argument("--rate-limit", type=float, default=1.0)
    probe_p.add_argument("--archive-dir", default=None)
    probe_p.add_argument("--allow-trading-hours", action="store_true")
    probe_p.add_argument("--timeout", type=float, default=5.0)
    probe_p.add_argument("--json", action="store_true")
    probe_p.set_defaults(func=_cmd_probe)

    # --- web / facade (migrated from cmds_web.py) ---

    ch_p = sub.add_parser("changes", help="盘中异动池")
    ch_p.add_argument("--types", default="")
    ch_p.add_argument("--page", type=int, default=1)
    ch_p.add_argument("--size", type=int, default=30)
    ch_p.add_argument("--json", action="store_true")
    ch_p.set_defaults(func=_cmd_changes)

    hot_p = sub.add_parser("hot", help="股吧个股人气榜")
    hot_p.add_argument("--page", type=int, default=1)
    hot_p.add_argument("--size", type=int, default=30)
    hot_p.add_argument("--json", action="store_true")
    hot_p.set_defaults(func=_cmd_hot)

    mg_p = sub.add_parser("margin", help="融资融券明细")
    mg_p.add_argument("symbol")
    mg_p.add_argument("--days", type=int, default=30)
    mg_p.add_argument("--timeout", type=float, default=5.0)
    mg_p.add_argument("--json", action="store_true")
    mg_p.set_defaults(func=_cmd_margin)

    sf_p = sub.add_parser("sector-flow", help="板块资金流")
    sf_p.add_argument("--board", default="industry")
    sf_p.add_argument("--sort", default="main_net")
    sf_p.add_argument("--limit", type=int, default=50)
    sf_p.add_argument("--timeout", type=float, default=5.0)
    sf_p.add_argument("--json", action="store_true")
    sf_p.set_defaults(func=_cmd_sector_flow)

    ab_p = sub.add_parser("adjusted-bars", help="复权 K 线")
    ab_p.add_argument("symbol")
    ab_p.add_argument("--method", default="qfq")
    ab_p.add_argument("--period", default="day")
    ab_p.add_argument("--count", type=int, default=320)
    ab_p.add_argument("--timeout", type=float, default=5.0)
    ab_p.add_argument("--json", action="store_true")
    ab_p.set_defaults(func=_cmd_adjusted_bars)

    am_p = sub.add_parser("all-market", help="全市场行情摘要")
    am_p.add_argument("--node", default="hs_a")
    am_p.add_argument("--source", default="sina")
    am_p.add_argument("--page-size", type=int, default=80)
    am_p.add_argument("--max-pages", type=int, default=None)
    am_p.add_argument("--timeout", type=float, default=5.0)
    am_p.add_argument("--json", action="store_true")
    am_p.set_defaults(func=_cmd_all_market)

    mk_p = sub.add_parser("minute-klines", help="分钟 K 线")
    mk_p.add_argument("symbol")
    mk_p.add_argument("--period", default="5min")
    mk_p.add_argument("--count", type=int, default=240)
    mk_p.add_argument("--timeout", type=float, default=5.0)
    mk_p.add_argument("--json", action="store_true")
    mk_p.set_defaults(func=_cmd_minute_klines)

    bd_p = sub.add_parser("baidu", help="百度财经源")
    bd_p.add_argument("symbol")
    bd_p.add_argument("--kind", default="kline", choices=("kline", "minute", "ticks", "quote"))
    bd_p.add_argument("--period", default="day")
    bd_p.add_argument("--count", type=int, default=320)
    bd_p.add_argument("--end-time", type=int, default=None)
    bd_p.add_argument("--limit", type=int, default=200)
    bd_p.add_argument("--timeout", type=float, default=5.0)
    bd_p.add_argument("--json", action="store_true")
    bd_p.set_defaults(func=_cmd_baidu)

    fund_p = sub.add_parser("fund", help="基金净值/估值/列表")
    fund_sub = fund_p.add_subparsers(dest="action", required=True)
    fund_nav = fund_sub.add_parser("nav")
    fund_nav.add_argument("code", nargs="?")
    fund_nav.add_argument("--page-size", type=int, default=100)
    fund_nav.add_argument("--page-index", type=int, default=1)
    fund_nav.add_argument("--json", action="store_true")
    fund_est = fund_sub.add_parser("estimate")
    fund_est.add_argument("code", nargs="?")
    fund_est.add_argument("--json", action="store_true")
    fund_list = fund_sub.add_parser("list")
    fund_list.add_argument("--json", action="store_true")
    fund_p.add_argument("--timeout", type=float, default=5.0)
    fund_p.set_defaults(func=_cmd_fund)

    idx_p = sub.add_parser("index", help="指数成分股")
    idx_sub = idx_p.add_subparsers(dest="action", required=True)
    idx_const = idx_sub.add_parser("constituents")
    idx_const.add_argument("code", nargs="?")
    idx_const.add_argument("--json", action="store_true")
    idx_p.add_argument("--timeout", type=float, default=5.0)
    idx_p.set_defaults(func=_cmd_index)

    # --- market (migrated from cmds_market.py) ---

    blk_p = sub.add_parser("blocks", help="板块行情")
    blk_p.add_argument("block_type")
    blk_p.add_argument("--count", type=int, default=1000)
    blk_p.add_argument("--timeout", type=float, default=5.0)
    blk_p.add_argument("--json", action="store_true")
    blk_p.set_defaults(func=_cmd_blocks)

    goods_p = sub.add_parser("goods", help="商品行情")
    goods_p.add_argument("symbol")
    goods_p.add_argument("--kind", default="quote", choices=("quote", "bars"))
    goods_p.add_argument("--period", default="day")
    goods_p.add_argument("--count", type=int, default=320)
    goods_p.add_argument("--timeout", type=float, default=5.0)
    goods_p.add_argument("--json", action="store_true")
    goods_p.set_defaults(func=_cmd_goods)

    f10_p = sub.add_parser("f10", help="F10 资料")
    f10_p.add_argument("symbol")
    f10_p.add_argument("--file", default=None)
    f10_p.add_argument("--timeout", type=float, default=5.0)
    f10_p.add_argument("--json", action="store_true")
    f10_p.set_defaults(func=_cmd_f10)

    list_p = sub.add_parser("list", help="代码表")
    list_p.add_argument("market", default="0")
    list_p.add_argument("--start", type=int, default=0)
    list_p.add_argument("--count", type=int, default=1000)
    list_p.add_argument("--timeout", type=float, default=5.0)
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=_cmd_list)

    qs_p = sub.add_parser("quotes-snapshot", help="批量快照")
    qs_p.add_argument("symbols", nargs="+")
    qs_p.add_argument("--timeout", type=float, default=5.0)
    qs_p.add_argument("--json", action="store_true")
    qs_p.set_defaults(func=_cmd_quotes_snapshot)

    return parser
