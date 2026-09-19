# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""CLI command handlers.

Data commands go through :class:`~tstdx.client.api.Client`. Six commands stay
on the transport client on purpose: ``probe`` drives the protocol prober,
``goods`` / ``f10`` use family-specific clients with multi-step flows, and
``blocks`` / ``list`` / ``quotes-snapshot`` page over raw calls and report raw
per-symbol failures. All of them resolve the config surface through
:func:`~tstdx.cli._common._transport_kwargs` so ``tstdx.toml`` stays true there
too.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from typing import Any

from .. import __version__
from ..client.api import Client
from ..errors import ValidationError
from ..integration.serialization import jsonable, serialize_result
from ..runtime.orchestration import FallbackPolicy
from ._common import (
    _client_kwargs,
    _fmt,
    _pct,
    _print_bars_table,
    _print_rows,
    _print_table,
    _resolve_hosts,
    _transport_kwargs,
    _transport_timeout,
)


def _print(value: Any) -> None:
    print(json.dumps(jsonable(value), ensure_ascii=False, indent=2, default=str))


def _policy(raw: str | None) -> FallbackPolicy | None:
    if not raw:
        return None
    return FallbackPolicy.build(*(item.strip() for item in raw.split(",") if item.strip()))


def cmd_version(args: Any) -> int:
    del args
    print(__version__)
    return 0


def cmd_capabilities(args: Any) -> int:
    del args
    _print({"capabilities": list(Client.capabilities())})
    return 0


def cmd_query(args: Any) -> int:
    try:
        call_args = json.loads(args.args_json)
        call_kwargs = json.loads(args.kwargs_json)
    except json.JSONDecodeError as exc:
        raise ValidationError("--args/--kwargs must be valid JSON") from exc
    if not isinstance(call_args, list):
        raise ValidationError("--args must decode to a JSON array")
    if not isinstance(call_kwargs, dict):
        raise ValidationError("--kwargs must decode to a JSON object")
    with Client(**_client_kwargs(args)) as client:
        _print(
            serialize_result(
                client.call(
                    args.capability,
                    *call_args,
                    provider=args.provider,
                    channel=args.channel,
                    currentness=args.currentness,
                    **call_kwargs,
                )
            )
        )
    return 0


def cmd_quotes(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(
            serialize_result(
                client.quotes(
                    args.symbols,
                    provider=args.provider,
                    policy=_policy(args.fallback),
                )
            )
        )
    return 0


def cmd_bars(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(
            serialize_result(
                client.bars(
                    args.symbol,
                    provider=args.provider,
                    policy=_policy(args.fallback),
                    period=args.period,
                    count=args.count,
                    start=args.start,
                    adjustment=args.adjustment,
                )
            )
        )
    return 0


def cmd_snapshot(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(serialize_result(client.snapshot(args.symbol, provider=args.provider)))
    return 0


def cmd_minute(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(serialize_result(client.minute(args.symbol, provider=args.provider)))
    return 0


def cmd_trades(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(
            serialize_result(
                client.trades(
                    args.symbol, provider=args.provider, start=args.start, count=args.count
                )
            )
        )
    return 0


def cmd_security_count(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(serialize_result(client.security_count(market=args.market, provider=args.provider)))
    return 0


def cmd_security_list(args: Any) -> int:
    with Client(**_client_kwargs(args)) as client:
        _print(
            serialize_result(
                client.security_list(market=args.market, start=args.start, provider=args.provider)
            )
        )
    return 0


def cmd_stream(args: Any) -> int:
    """流式订阅（同步 QuoteStream；退出码与 quotes-snapshot 同步）。

    使用 :class:`tstdx.streaming.QuoteStream` 作为可注入的流式实现，便于
    离线测试通过 monkeypatch ``tstdx.streaming.QuoteStream`` 替换。
    """
    from ..streaming import QuoteStream

    syms = args.symbols
    stream = QuoteStream(provider=args.provider, **_transport_kwargs(args))
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
            max_queue=args.max_queue,
            on_quote=_on_quote,
            on_error=_on_error,
        )
        stream.start()
        print(
            f"流式订阅已启动（{len(syms)} 只，间隔 {args.interval}s，"
            f"{'仅变化' if args.diff else '全量'}），按 Ctrl+C 停止 ..."
        )
        with contextlib.suppress(KeyboardInterrupt):  # pragma: no cover
            time.sleep(args.seconds)
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


# ---------------------------------------------------------------------------
# v12 command handlers migrated from cli/cmds_hosts.py, cmds_market.py,
# cmds_web.py (v15 Phase 3 consolidation). These re-export their original
# ``_cmd_*`` names from ``tstdx.cli`` so legacy tests keep working.
# ---------------------------------------------------------------------------


def _parse_cmd_number(text: str) -> int:
    """解析命令号：``0x053e`` / ``053e``（十六进制）/ ``1342``（十进制）。"""
    s = str(text).strip()
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s, 10)
    except ValueError as exc:
        raise ValueError(f"非法命令号: {text!r}（示例：0x053e 或 1342）") from exc


# --- hosts / transport (from cmds_hosts.py) ---


def _cmd_hosts_audit(args: Any) -> int:
    """``tstdx hosts audit``：调用 wheel 内置的 5 族主站巡检实现。"""
    from ..tools.host_audit import main as host_audit_main

    argv: list[str] = []
    for family in getattr(args, "family", None) or ():
        argv.extend(["--family", family])
    argv.append(f"--timeout={args.timeout}")
    if getattr(args, "samples", None) is not None:
        argv.append(f"--samples={args.samples}")
    if getattr(args, "workers", None) is not None:
        argv.append(f"--workers={args.workers}")
    if getattr(args, "report", None):
        argv.append(f"--report={args.report}")
    if getattr(args, "markdown", None):
        argv.append(f"--markdown={args.markdown}")
    if getattr(args, "ranking_file", None):
        argv.append(f"--ranking-file={args.ranking_file}")
    if getattr(args, "hosts_file", None):
        argv.append(f"--hosts-file={args.hosts_file}")
    if getattr(args, "quiet", False):
        argv.append("--quiet")
    if getattr(args, "strict", False):
        argv.append("--strict")
    if getattr(args, "no_save_ranking", False):
        argv.append("--no-save-ranking")
    return int(host_audit_main(argv))


def _cmd_hosts_scan(args: Any) -> int:
    """``tstdx hosts scan``：并发测速内置候选池并写排名文件。"""
    from ..transport.hosts import resolve_hosts
    from ..transport.speedtest import speedtest_and_save

    entries = resolve_hosts(None, family="quotation")
    results = speedtest_and_save(
        entries,
        family="quotation",
        timeout=args.timeout,
        progress=True,
    )
    rows = []
    for r in results:
        rtt = f"{r.rtt_ms:.1f}ms" if r.rtt_ms is not None else "-"
        rows.append([f"{r.host}:{r.port}", "OK" if r.ok else "ERR", rtt, r.error or ""])
    _print_table(["host", "state", "rtt", "error"], rows)
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} 主站可达，已写入 ~/.tstdx/server_ranking.json")
    return 0


def _cmd_hosts_list(args: Any) -> int:
    """``tstdx hosts list``：查看当前生效的主站池（含排名文件合并结果）。"""
    from ..transport.hosts import resolve_hosts

    entries = resolve_hosts(None, family="quotation")
    rows = []
    for e in entries:
        rtt = f"{e.rtt_ms:.1f}ms" if e.rtt_ms is not None else "-"
        rows.append([e.key, e.family, rtt, "verified" if getattr(e, "verified", False) else ""])
    _print_table(["host", "family", "rtt", "verified"], rows)
    print(f"\n共 {len(entries)} 台主站")
    return 0


def _cmd_server_test(args: Any) -> int:
    """``tstdx server-test``：主站连通性测速。"""
    from ..transport.hosts import resolve_hosts
    from ..transport.speedtest import speedtest

    entries = resolve_hosts(None, family="quotation")
    results = speedtest(entries, family="quotation", timeout=args.timeout, progress=True)
    rows = []
    for r in results:
        rtt = f"{r.rtt_ms:.1f}ms" if r.rtt_ms is not None else "-"
        rows.append([f"{r.host}:{r.port}", "OK" if r.ok else "ERR", rtt, r.error or ""])
    _print_table(["host", "state", "rtt", "error"], rows)
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok}/{len(results)} 主站可达")
    return 0


def _cmd_serve(args: Any) -> int:
    """启动 HTTP 行情网关（40+ 端点 + WebSocket）。

    复用 :func:`tstdx.integration.runtime_http.create_runtime_app` 的 uvicorn 启动逻辑；
    ``--port 0`` 是合法值（OS 分配随机空闲端口），不得被 ``or 8000`` 短路。
    """
    from ..integration.runtime_http import create_runtime_app

    try:
        import uvicorn
    except ImportError:  # pragma: no cover —— 依赖检查
        print("uvicorn 未安装: pip install tstdx[server]", file=sys.stderr)
        return 2
    app = create_runtime_app()
    port = getattr(args, "port", None)
    uvicorn.run(
        app,
        host=getattr(args, "bind", None) or "127.0.0.1",
        port=8000 if port is None else int(port),
    )
    return 0  # pragma: no cover —— uvicorn.run 阻塞至退出


def _cmd_feedback(args: Any) -> int:
    """反馈上报组：``tstdx feedback submit`` / ``tstdx feedback stats``。"""
    from ..errors import TdxError
    from ..feedback import FeedbackReporter, UserStats

    if args.feedback_command == "submit":
        reporter = FeedbackReporter(endpoint=args.endpoint, store_dir=args.store_dir)
        ok = reporter.report_error(TdxError(args.message), context={"source": "cli"})
        if ok:
            print("反馈已提交（已脱敏）。")
            return 0
        print(
            "反馈未发送：默认禁用。设置 TSTDX_FEEDBACK=1 启用，"
            "或 TSTDX_FEEDBACK=dry-run 调试预览。",
            file=sys.stderr,
        )
        return 1

    snapshot = UserStats().snapshot()
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    _print_table(
        ["field", "value"],
        [[k, _fmt(v)] for k, v in snapshot.items()],
    )
    return 0


def _cmd_probe(args: Any) -> int:
    """未知命令主动探测（薄壳接线 :mod:`tstdx.protocol.prober`）。"""
    from ..client import TdxClient
    from ..errors import TdxError
    from ..protocol.prober import Prober

    try:
        cmd_id = _parse_cmd_number(args.cmd)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("用法：tstdx probe 0x053e --market 1 --code 600519", file=sys.stderr)
        return 2

    rate = min(max(float(args.rate_limit), 0.1), 5.0)
    conn = _transport_kwargs(args)

    try:
        with TdxClient(**conn) as client:
            prober = Prober(
                client=client,
                rate_limit=rate,
                archive_dir=args.archive_dir,
                timeout=conn["timeout"],
                block_offline_only=not args.allow_trading_hours,
            )
            result = prober.probe_command(cmd_id, market=args.market, code=args.code)
            if result.ok:
                try:
                    prober.archive(result)
                except OSError as exc:
                    result.notes.append(f"archive 写入失败: {exc}")
    except TdxError as exc:
        print(f"错误：探测失败 —— {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 0 if result.ok else 1
    print(f"命令 0x{cmd_id:04X} 探测完成")
    print(f"  frame_size          : {result.frame_size}")
    print(f"  plausible_record    : {result.plausible_record_size}")
    print(f"  ok                  : {result.ok}")
    if result.notes:
        print(f"  notes               : {'; '.join(result.notes)}")
    if result.draft_path:
        print(f"  draft               : {result.draft_path}")
    return 0 if result.ok else 1


# --- web / migrated capabilities ---


class _ClientRows:
    """Adapter turning ``Client`` :class:`QueryResult` envelopes into raw rows.

    The CLI commands below print plain rows, so every migrated capability call
    is unwrapped here instead of at each call site.
    """

    def __init__(self, *, timeout: float | None = None, hosts: Any | None = None) -> None:
        from ..client.api import Client

        self._client = Client(hosts=hosts, timeout=timeout)

    def __enter__(self) -> _ClientRows:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.close()

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            result = getattr(self._client, name)(*args, **kwargs)
            return getattr(result, "data", result)

        call.__name__ = name
        return call


def _cmd_changes(args: Any) -> int:
    """盘中异动池（东财 push2ex getAllStockChanges；走统一内核的 Web 能力）。"""
    try:
        types = [int(t) for t in str(args.types).split(",") if t.strip()]
        with _ClientRows(**_client_kwargs(args)) as api:
            rows = api.stock_changes(types, page=args.page, size=args.size)
    except ValueError as exc:
        print(f"错误：--types 必须是逗号分隔整数 —— {exc}", file=sys.stderr)
        print("用法：tstdx changes --types 8201,8193 [--page N] [--size N]", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 内核异常统一出口
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


def _cmd_hot(args: Any) -> int:
    """股吧个股人气榜（东财 emappdata stockrank；走统一内核的 Web 能力）。"""
    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            rows = api.hot_rank(page=args.page, size=args.size)
    except Exception as exc:  # noqa: BLE001 - 内核异常统一出口
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


def _cmd_margin(args: Any) -> int:
    """个股融资融券明细：``tstdx margin <symbol> [--days N]``（东财 Web 源）。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            rows = to_dicts(api.margin(args.symbol, days=args.days))
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


def _cmd_sector_flow(args: Any) -> int:
    """板块资金流排行：``tstdx sector-flow [--board industry] [--sort main_net]``。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            rows = to_dicts(api.sector_flow(args.board, sort=args.sort, limit=args.limit))
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


def _cmd_adjusted_bars(args: Any) -> int:
    """复权 K 线：``tstdx adjusted-bars <symbol> [--method qfq|hfq|fixed|none]``。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


def _cmd_all_market(args: Any) -> int:
    """全市场行情摘要：``tstdx all-market [--node hs_a] [--source sina|tencent]``。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


def _cmd_minute_klines(args: Any) -> int:
    """分钟 K 线：``tstdx minute-klines <symbol> [--period 5min] [--count 240]``。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


def _cmd_baidu(args: Any) -> int:
    """百度财经源：``tstdx baidu <symbol> [--kind kline|minute|ticks|quote]``。"""
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


def _cmd_fund(args: Any) -> int:
    """东财基金源：``tstdx fund nav|estimate|list``。"""
    if args.action in ("nav", "estimate") and not args.code:
        print(f"错误：fund {args.action} 需要 <code>（6 位基金代码）", file=sys.stderr)
        return 2
    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


def _cmd_index(args: Any) -> int:
    """东财指数成分股：``tstdx index constituents <code>``。"""
    if args.action == "constituents" and not args.code:
        print(
            "错误：index constituents 需要 <code>（指数代码，如 000300 / 000905 / 930050）",
            file=sys.stderr,
        )
        return 2
    try:
        with _ClientRows(**_client_kwargs(args)) as api:
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


# --- market (from cmds_market.py) ---


def _cmd_blocks(args: Any) -> int:
    """板块行情：``tstdx blocks <block_type> [--count N]``。"""
    from ..client import TdxClient

    try:
        with TdxClient(**_transport_kwargs(args)) as c:
            rows: list[dict[str, Any]] = []
            for start in range(0, args.count, 1000):
                page = c.block_quotes(args.block_type, start=start)
                rows.extend(page)
                if len(page) < 1000:
                    break
    except Exception as exc:  # noqa: BLE001
        print(f"错误：板块行情获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("无板块行情返回")
        return 0
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    _print_rows(rows)
    return 0


def _cmd_goods(args: Any) -> int:
    """商品行情：``tstdx goods <symbol> [--kind quote|bars]``。"""
    from ..client import get_client

    try:
        with get_client("goods", hosts=_resolve_hosts(args), timeout=_transport_timeout(args)) as c:
            if args.kind == "quote":
                data = c.goods_quote(args.symbol, as_format="dict")
            else:
                data = c.goods_bars(
                    args.symbol, period=args.period, count=args.count, as_format="dict"
                )
    except Exception as exc:  # noqa: BLE001
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


def _cmd_f10(args: Any) -> int:
    """F10 资料：``tstdx f10 <symbol>`` 列栏目目录；``--file <name>`` 下载并解析正文。"""
    from ..client import get_client

    try:
        with get_client("f10", hosts=_resolve_hosts(args), timeout=_transport_timeout(args)) as c:
            if args.file:
                sections = c.parse_text(c.download(args.symbol, args.file))
                data = [{"title": s.title, "text": s.text} for s in sections]
            else:
                data = c.catalog(args.symbol)
    except Exception as exc:  # noqa: BLE001
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


def _cmd_list(args: Any) -> int:
    """代码表：``tstdx list <market> [--start N] [--count N]``（分页起始偏移）。"""
    from ..client import _PREFIX_MARKET, TdxClient

    market = args.market
    if market in ("sh", "sz", "bj"):
        market = _PREFIX_MARKET[market]
    try:
        with TdxClient(**_transport_kwargs(args)) as c:
            rows: list[dict[str, Any]] = []
            for start in range(int(args.start), int(args.start) + int(args.count), 1000):
                page = c.security_list(int(market), start=start)
                rows.extend(page)
                if len(page) < 1000:
                    break
    except Exception as exc:  # noqa: BLE001
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


def _cmd_quotes_snapshot(args: Any) -> int:
    """批量快照：``tstdx quotes-snapshot <symbols...>``（全失败 → exit 1）。"""
    from ..client import TdxClient

    last_errors: list[tuple[str, BaseException]] = []
    try:
        with TdxClient(**_transport_kwargs(args)) as c:
            quotes = c.quotes_snapshot(args.symbols)
            last_errors = list(getattr(c, "last_errors", []))
    except Exception as exc:  # noqa: BLE001
        print(f"错误：在线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if not quotes:
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
