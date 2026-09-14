# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License
"""主站/服务/工具子命令：server-test/hosts/serve/feedback/probe。

REFACTOR_PLAN_v8 P3：自 ``tstdx/cli.py`` 拆出，行为不变。
"""

from __future__ import annotations

import argparse
import json
import sys

from ..errors import TdxError
from ._common import _fmt, _print_table, _resolve_hosts

__all__ = [
    "_cmd_feedback",
    "_cmd_hosts_audit",
    "_cmd_hosts_list",
    "_cmd_hosts_scan",
    "_cmd_probe",
    "_cmd_serve",
    "_cmd_server_test",
    "_parse_cmd_number",
]


def _cmd_server_test(args: argparse.Namespace) -> int:
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


def _cmd_hosts_scan(args: argparse.Namespace) -> int:
    """``tstdx hosts scan``：并发测速内置候选池并写排名文件（U2 建议命令）。"""
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


def _cmd_hosts_list(args: argparse.Namespace) -> int:
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


def _cmd_hosts_audit(args: argparse.Namespace) -> int:
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


def _cmd_serve(args: argparse.Namespace) -> int:
    """启动 HTTP 行情网关（40+ 端点 + WebSocket，准确数量以启动日志为准）。

    复用 :func:`tstdx.integration.runtime_http.create_runtime_app` 的 uvicorn 启动逻辑；
    ``--host/--port`` 可由命令行覆盖（缺省 127.0.0.1:8000）。
    """
    from ..integration.runtime_http import create_runtime_app

    try:
        import uvicorn
    except ImportError:  # pragma: no cover —— 依赖检查
        print("uvicorn 未安装: pip install tstdx[server]", file=sys.stderr)
        return 2
    app = create_runtime_app()
    # --port 0 是合法值（OS 分配随机空闲端口），不得被 `or 8000` 短路（审计 §3-3）
    port = getattr(args, "port", None)
    uvicorn.run(
        app,
        host=getattr(args, "bind", None) or "127.0.0.1",
        port=8000 if port is None else int(port),
    )
    return 0  # pragma: no cover —— uvicorn.run 阻塞至退出


def _cmd_feedback(args: argparse.Namespace) -> int:
    """反馈上报组：``tstdx feedback submit`` / ``tstdx feedback stats``。

    薄壳接线 :mod:`tstdx.feedback` 公开 API（FeedbackReporter / UserStats），
    不复述其脱敏逻辑。``submit`` 默认不发（需 ``TSTDX_FEEDBACK=1`` 或
    ``TSTDX_FEEDBACK=dry-run``），禁用态提示并以退出码 1 结束。
    """
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
    print("用户本地统计（按进程独立）:")
    _print_table(
        ["field", "value"],
        [[k, _fmt(v)] for k, v in snapshot.items()],
    )
    return 0


def _parse_cmd_number(text: str) -> int:
    """解析命令号：``0x053e`` / ``053e``?（按十进制）/ ``1342``（十进制）。"""
    s = str(text).strip()
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s, 10)
    except ValueError as exc:
        raise ValueError(f"非法命令号: {text!r}（示例：0x053e 或 1342）") from exc


def _cmd_probe(args: argparse.Namespace) -> int:
    """未知命令主动探测（薄壳接线 :mod:`tstdx.protocol.prober`，孤儿接线闭环）。

    合规约束由 Prober 保证：非交易时段门禁（``--allow-trading-hours`` 显式
    放行）、默认速率 1 req/s（``--rate-limit`` 钳制在 [0.1, 5]）、归档到
    ``PROTOCOL_SPEC/UNKNOWN/`` 或 ``--archive-dir`` 指定目录。
    """
    from ..client import TdxClient
    from ..protocol.prober import Prober

    try:
        cmd_id = _parse_cmd_number(args.cmd)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("用法：tstdx probe 0x053e --market 1 --code 600519", file=sys.stderr)
        return 2

    rate = min(max(float(args.rate_limit), 0.1), 5.0)

    try:
        with TdxClient(hosts=_resolve_hosts(args), timeout=args.timeout) as client:
            prober = Prober(
                client=client,
                rate_limit=rate,
                archive_dir=args.archive_dir,
                timeout=args.timeout,
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
