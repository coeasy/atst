# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Argument parser for the v13 Client-first CLI."""

from __future__ import annotations

import argparse

from .runtime_commands import (
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


def _provider_args(parser: argparse.ArgumentParser, *, fallback: bool = False) -> None:
    parser.add_argument("--provider", default=None if fallback else "tdx")
    if fallback:
        parser.add_argument("--fallback", help="explicit comma-separated Provider order, e.g. tdx,tencent,sina")
    parser.add_argument("--no-cache", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tstdx", description="tstdx v13 Provider-first market-data client")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("version", help="show package version")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("capabilities", help="list migrated v13 business capabilities")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("query", help="execute any migrated v13 capability")
    p.add_argument("capability")
    p.add_argument("--provider")
    p.add_argument("--channel")
    p.add_argument("--currentness", default="business", choices=("auto", "live", "historical", "business"))
    p.add_argument("--max-age", type=float)
    p.add_argument("--args", dest="args_json", default="[]", help="JSON array of positional arguments")
    p.add_argument("--kwargs", dest="kwargs_json", default="{}", help="JSON object of keyword arguments")
    p.add_argument("--no-cache", action="store_true")
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
    p.add_argument("--diff-only", action="store_true")
    p.add_argument("--max-queue", type=int, default=1024)
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_stream)

    return parser
