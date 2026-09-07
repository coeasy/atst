# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License
"""行情主通路子命令：bars/quotes/count/info/list/trades/blocks/goods/f10/stream。

REFACTOR_PLAN_v8 P3：自 ``tstdx/cli.py`` 拆出，行为不变。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..errors import TdxError
from ._common import _fmt, _pct, _print_rows, _print_table, _resolve_hosts

__all__ = [
    "_cmd_bars",
    "_cmd_blocks",
    "_cmd_count",
    "_cmd_f10",
    "_cmd_goods",
    "_cmd_info",
    "_cmd_list",
    "_cmd_quotes",
    "_cmd_quotes_snapshot",
    "_cmd_stream",
    "_cmd_trades",
    "_cmd_version",
]


def _cmd_version(args: argparse.Namespace) -> int:
    import tstdx

    print(f"tstdx {tstdx.__version__}")
    return 0


def _cmd_bars(args: argparse.Namespace) -> int:
    if getattr(args, "source", None) in ("router", "cache"):
        return _cmd_bars_router(args)
    from ..client import TdxClient

    hosts = _resolve_hosts(args)
    try:
        with TdxClient(hosts=hosts, timeout=args.timeout) as c:
            bars = c.bars(args.symbol, period=args.period, count=args.count, as_format="dict")
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(bars, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {args.period} 共 {len(bars)} 根")
    rows = [
        [
            b["datetime"],
            _fmt(b["open"]),
            _fmt(b["high"]),
            _fmt(b["low"]),
            _fmt(b["close"]),
            int(b["volume"]),
            _fmt(b["amount"]),
        ]
        for b in bars
    ]
    _print_table(["datetime", "open", "high", "low", "close", "volume", "amount"], rows)
    return 0


def _cmd_bars_router(args: argparse.Namespace) -> int:
    """离线 / 降级路径：走 DataSourceRouter（默认 cache 源回放 golden 样本）。"""
    from ..config.schema import SourcesConfig
    from ..sources import DataSourceRouter

    cfg = SourcesConfig(
        order=["cache", "tdx", "web", "reader"],
        enabled={"cache": True, "tdx": True, "web": True, "reader": True},
    )
    router = DataSourceRouter(
        config=cfg, golden_root=getattr(args, "golden", None) or "tests/golden"
    )
    try:
        bars = router.kline(args.symbol, period=args.period, count=args.count, as_format="dict")
    except TdxError as exc:
        print(f"错误：路由获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(bars, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {args.period} 共 {len(bars)} 根（源：{router.last_source}）")
    rows = [
        [
            b["datetime"],
            _fmt(b["open"]),
            _fmt(b["high"]),
            _fmt(b["low"]),
            _fmt(b["close"]),
            int(b["volume"]),
            _fmt(b["amount"]),
        ]
        for b in bars
    ]
    _print_table(["datetime", "open", "high", "low", "close", "volume", "amount"], rows)
    return 0


def _cmd_quotes(args: argparse.Namespace) -> int:
    if getattr(args, "source", None) in ("router", "cache"):
        return _cmd_quotes_router(args)
    from ..client import TdxClient

    hosts = _resolve_hosts(args)
    last_errors: list[tuple[str, BaseException]] = []
    try:
        with TdxClient(hosts=hosts, timeout=args.timeout) as c:
            quotes = c.quotes(args.symbols, as_format="dict")
            last_errors = list(getattr(c, "last_errors", []))
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not quotes:
        detail = "; ".join(f"{s}: {e}" for s, e in last_errors) if last_errors else "未知原因"
        print(f"错误：未获取到任何行情（{detail}）", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(quotes, ensure_ascii=False, default=str))
        return 0
    rows = [
        [
            q.get("code", ""),
            _fmt(q["price"]),
            _fmt(q.get("pct_change", 0), 2) + "%",
            _fmt(q["open"]),
            _fmt(q["high"]),
            _fmt(q["low"]),
            int(q["volume"]),
            _fmt(q["amount"]),
        ]
        for q in quotes
    ]
    _print_table(["code", "price", "pct", "open", "high", "low", "volume", "amount"], rows)
    return 0


def _cmd_quotes_router(args: argparse.Namespace) -> int:
    """离线 / 降级路径：走 DataSourceRouter（默认 cache 源回放 golden 样本）。"""
    from ..config.schema import SourcesConfig
    from ..sources import DataSourceRouter

    cfg = SourcesConfig(
        order=["cache", "tdx", "web"],
        enabled={"cache": True, "tdx": True, "web": True},
    )
    router = DataSourceRouter(
        config=cfg, golden_root=getattr(args, "golden", None) or "tests/golden"
    )
    try:
        quotes = router.quotes(args.symbols, as_format="dict")
    except TdxError as exc:
        print(f"错误：路由获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not quotes:
        print("错误：未获取到任何行情（golden 缓存中可能无对应样本）", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(quotes, ensure_ascii=False, default=str))
        return 0
    rows = [
        [
            q.get("code", ""),
            _fmt(q["price"]),
            _fmt(_pct(q)) + "%",
            _fmt(q["open"]),
            _fmt(q["high"]),
            _fmt(q["low"]),
            int(q["volume"]),
            _fmt(q["amount"]),
        ]
        for q in quotes
    ]
    _print_table(["code", "price", "pct", "open", "high", "low", "volume", "amount"], rows)
    return 0


def _cmd_count(args: argparse.Namespace) -> int:
    from ..client import _PREFIX_MARKET, TdxClient

    market = args.market
    if market in ("sh", "sz", "bj"):
        market = _PREFIX_MARKET[market]  # 单一事实源（v5 DC1：bj→2）
    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            n = c.security_count(int(market))
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    print(f"市场 {args.market} 证券总数：{n}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    from ..client import TdxClient

    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            info = c.finance_info(args.symbol)
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not info:
        print("无财务信息返回")
        return 0
    if args.json:
        print(json.dumps(info, ensure_ascii=False, default=str))
        return 0
    rows = [[k, _fmt(v)] for k, v in info.items()]
    _print_table(["field", "value"], rows)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from ..client import _PREFIX_MARKET, TdxClient

    market = args.market
    if market in ("sh", "sz", "bj"):
        market = _PREFIX_MARKET[market]  # 单一事实源（v5 DC1：bj→2）
    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            rows: list[dict[str, Any]] = []
            # --start 接线：作为分页起始偏移（此前是死参数，审计 §3-3）
            for start in range(int(args.start), int(args.start) + int(args.count), 1000):
                page = c.security_list(int(market), start=start)
                rows.extend(page)
                if len(page) < 1000:
                    break
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("无代码表返回")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# 市场 {args.market} 代码表共 {len(rows)} 条")
    _print_rows(rows)
    return 0


def _cmd_quotes_snapshot(args: argparse.Namespace) -> int:
    from ..client import TdxClient

    last_errors: list[tuple[str, BaseException]] = []
    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            quotes = c.quotes_snapshot(args.symbols)
            # 只消费 getattr 现契约：client 的 last_errors 语义由 client 域维护
            last_errors = list(getattr(c, "last_errors", []))
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not quotes:
        # 全失败（空结果 + last_errors 明细）→ exit 1 + stderr 错误摘要（V5）
        detail = "; ".join(f"{s}: {e}" for s, e in last_errors) or "未知原因"
        print(
            f"错误：批量快照全部失败（{len(args.symbols)} 只，0 成功）——{detail}", file=sys.stderr
        )
        return 1
    if args.json:
        print(json.dumps(quotes, ensure_ascii=False, default=str))
        return 0
    rows = [
        [
            q.get("code", ""),
            _fmt(q.get("price", 0)),
            _fmt(_pct(q)) + "%",
            _fmt(q.get("open", 0)),
            _fmt(q.get("high", 0)),
            _fmt(q.get("low", 0)),
            int(q.get("volume", 0)),
            _fmt(q.get("amount", 0)),
        ]
        for q in quotes
    ]
    _print_table(["code", "price", "pct", "open", "high", "low", "volume", "amount"], rows)
    return 0


def _cmd_trades(args: argparse.Namespace) -> int:
    from ..client import TdxClient

    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            trades = c.trade_today(args.symbol, start=args.start, count=args.count)
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not trades:
        print("无逐笔成交返回")
        return 0
    if args.json:
        print(json.dumps(trades, ensure_ascii=False, default=str))
        return 0
    _print_rows(trades)
    return 0


def _cmd_blocks(args: argparse.Namespace) -> int:
    from ..client import TdxClient

    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            rows: list[dict[str, Any]] = []
            for start in range(0, args.count, 1000):
                page = c.block_quotes(args.block_type, start=start)
                rows.extend(page)
                if len(page) < 1000:
                    break
    except TdxError as exc:
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("无板块行情返回")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    _print_rows(rows)
    return 0


def _cmd_goods(args: argparse.Namespace) -> int:
    from ..client import get_client

    try:
        with get_client("goods", hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            if args.kind == "quote":
                data = c.goods_quote(args.symbol, as_format="dict")
            else:
                data = c.goods_bars(
                    args.symbol, period=args.period, count=args.count, as_format="dict"
                )
    except TdxError as exc:
        print(f"错误：商品行情获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not data:
        print("无商品数据返回")
        return 0
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    if args.kind == "quote":
        _print_rows(data if isinstance(data, list) else [data])
    else:
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
    return 0


def _cmd_f10(args: argparse.Namespace) -> int:
    """F10 资料：``tstdx f10 <symbol>`` 列栏目目录；``--file <name>`` 下载并解析正文。"""
    from ..client import get_client

    try:
        with get_client("f10", hosts=_resolve_hosts(args), timeout=args.timeout) as c:
            if args.file:
                sections = c.parse_text(c.download(args.symbol, args.file))
                data = [{"title": s.title, "text": s.text} for s in sections]
            else:
                data = c.catalog(args.symbol)
    except TdxError as exc:
        print(f"错误：F10 获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not data:
        print("无 F10 数据返回")
        return 0
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    if args.file:
        for sec in data:
            print(f"【{sec['title']}】")
            print(sec["text"])
        return 0
    _print_rows([{"title": r.get("title", ""), "filename": r.get("filename", "")} for r in data])
    return 0


def _cmd_stream(args: argparse.Namespace) -> int:
    from ..streaming import QuoteStream

    syms = args.symbols
    stream = QuoteStream(hosts=_resolve_hosts(args), timeout=args.timeout)
    counts = {"quote": 0, "error": 0}

    def _on_quote(code: str, q: dict[str, Any]) -> None:
        counts["quote"] += 1
        print(f"[{code}] price={q.get('price')} pct={_pct(q):.2f}% vol={q.get('volume')}")

    def _on_error(e: BaseException) -> None:
        counts["error"] += 1
        print(f"[error] {e}", file=sys.stderr)

    try:
        stream.subscribe(
            syms,
            interval=args.interval,
            diff_only=args.diff,
            on_quote=_on_quote,
            on_error=_on_error,
        )
        stream.start()
        print(
            f"流式订阅已启动（{len(syms)} 只，间隔 {args.interval}s，"
            f"{'仅变化' if args.diff else '全量'}），按 Ctrl+C 停止 ..."
        )
        try:
            import time as _t

            _t.sleep(args.seconds)
        except KeyboardInterrupt:  # pragma: no cover
            pass
    finally:
        stream.stop()
    # 失败退出码与 quotes-snapshot 同步（V5）：一条数据都没收到 → exit 1
    if counts["quote"] == 0:
        print(
            f"错误：流式订阅 {args.seconds}s 内未收到任何行情（错误 {counts['error']} 次）",
            file=sys.stderr,
        )
        return 1
    return 0
