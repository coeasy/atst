# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI command handlers.

Data commands go through :class:`~atst.client.api.Client`. Six commands stay
on the transport client on purpose: ``probe`` drives the protocol prober,
``goods`` / ``f10`` use family-specific clients with multi-step flows, and
``blocks`` / ``list`` / ``quotes-snapshot`` page over raw calls and report raw
per-symbol failures. All six take their connection kwargs from
:func:`~atst.cli._common._transport_kwargs` (the same config→transport
translation the kernel's executor uses, so one TOML key cannot mean two things)
and all six hand their ``get_client(...)`` result to
:func:`~atst.cli._common.family_client`, the CLI's one protected release region.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..client import get_client
from ..client.api import Client
from ..errors import ValidationError
from ..runtime.orchestration import FallbackPolicy
from ..server.serialization import jsonable, serialize_result
from ..server.wire_fields import reject_reserved_kwargs
from ._common import (
    _client_kwargs,
    _fmt,
    _pct,
    _print_bars_table,
    _print_rows,
    _print_table,
    _transport_kwargs,
    family_client,
    validate_period,
)


def _print(value: Any) -> None:
    print(json.dumps(jsonable(value), ensure_ascii=False, indent=2, default=str))


def _policy(raw: str | None) -> FallbackPolicy | None:
    """``--fallback`` 的解法在内核那一格（:meth:`FallbackPolicy.from_wire`），这里不抄第二份。"""
    return FallbackPolicy.from_wire(raw)


def cmd_version(args: Any) -> int:
    del args
    print(__version__)
    return 0


def cmd_capabilities(args: Any) -> int:
    """列出 / 检索能力。默认输出保持纯名字形状；``--describe`` 才给健康度细节。"""
    describe = getattr(args, "describe", None)
    query = getattr(args, "query", "") or ""
    status = getattr(args, "status", None)
    provider = getattr(args, "provider", None)
    if describe is not None:
        from ..providers.discovery import describe_capability

        _print({"capability": describe_capability(describe)})
        return 0
    if query or status is not None or provider is not None:
        from ..providers.discovery import search_capabilities

        limit = getattr(args, "limit", 50)
        records = search_capabilities(
            query,
            status=status,
            provider=provider,
            limit=limit,
        )
        _print({"capabilities": [item["name"] for item in records]})
        return 0
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
    reject_reserved_kwargs(
        face="cli_query",
        where="atst query 的 --kwargs",
        kwargs=call_kwargs,
    )
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
                    strict=args.strict,
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
                    currentness=args.currentness,
                    strict=args.strict,
                    start_date=args.start_date,
                    end_date=args.end_date,
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
    """流式订阅：编译与执行都在 ``Client.stream`` 里，CLI 只打印与计数。

    退出码与 quotes-snapshot 同步（V5）：一条数据都没收到 → exit 1。

    ``--seconds`` 是这次订阅**保持**的时长，默认 10 秒；给 0 或负数等于要求"订阅零秒并收到
    行情"，那按构造只能以失败收场，所以在这里就地判死（E1010 / 退出码 2），而不是让它跑完
    再回一句"0.0s 内未收到任何行情"。
    """
    if float(args.seconds) <= 0:
        raise ValidationError(
            "--seconds 必须为正数（默认 10）：零或负数的订阅窗口收不到任何行情",
            context={"phase": "wire_validation", "seconds": args.seconds},
        )
    counts = {"quote": 0, "error": 0}

    def _on_quote(code: str, q: dict[str, Any]) -> None:
        counts["quote"] += 1
        print(f"[{code}] price={q.get('price')} pct={_pct(q):.2f}% vol={q.get('volume')}")

    def _on_error(e: BaseException) -> None:
        counts["error"] += 1
        print(f"[error] {e}", file=sys.stderr)

    with Client(**_client_kwargs(args)) as client:
        stream = client.stream(
            args.symbols,
            provider=args.provider,
            interval=args.interval,
            diff_only=args.diff,
            max_queue=args.max_queue,
            on_quote=_on_quote,
            on_error=_on_error,
        )
        try:
            stream.start()
            print(
                f"流式订阅已启动（{len(args.symbols)} 只，间隔 {args.interval}s，"
                f"{'仅变化' if args.diff else '全量'}），按 Ctrl+C 停止 ..."
            )
            with contextlib.suppress(KeyboardInterrupt):  # pragma: no cover
                time.sleep(args.seconds)
        finally:
            stream.stop()
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
# ``_cmd_*`` names from ``atst.cli`` so legacy tests keep working.
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
    """``atst hosts audit``：调用 wheel 内置的 5 族主站巡检实现。"""
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
    """``atst hosts scan``：并发测速**整个候选池**并写排名文件。

    候选池 = 内置池 ＋ 排名文件里的 extras（发现进来的主机）＋ ``ATST_HOSTS``（存在时
    替换内置池）。这里刻意**不**扫 ``resolve_hosts()`` 的 top-8：排名是证据，只扫当前
    前八会让掉出前八的主站永远拿不到新证据、再也回不来（自我锁死）。

    ``extras`` 必须一起扫：``hosts discover`` 把它们写进排名之后，如果 scan 看不见，
    这些主机就只剩"被采纳那一刻"的 RTT——掉线了也不会被淘汰，成为冷启动超时的来源。
    scan 的失败项会走 ``merge()`` 的清除分支，因此这一条命令同时完成"刷新 + 淘汰"。

    只覆盖 ``quotation``（STANDARD）族：排名文件本身也只记这一族。其余四族
    （extended / mac / goods / f10）的巡检走 ``atst hosts audit --family``，
    所以结论行必须写出族名——否则会被读成"全部主站都测过了"。
    """
    from ..transport.hosts import RankingStore, resolve_hosts
    from ..transport.speedtest import speedtest_and_save

    ranking_file = str(getattr(args, "ranking_file", "~/.atst/server_ranking.json"))
    # 先取"非排名"的那一半（内置池，或 ATST_HOSTS 覆盖后的名单），再用排名文件补 extras。
    # 不走 ``resolve_hosts(use_ranking=True, max_hosts=64)``：候选超过 64 台时它会按 score
    # 截断，而被截掉的恰好是"从没测过、score=1e6 排最后"的那批——它们最需要这次探测。
    base = resolve_hosts(None, family="quotation", use_ranking=False, max_hosts=64)
    entries = list(base)
    if not os.environ.get("ATST_HOSTS"):
        seen = {entry.key for entry in entries}
        entries.extend(
            entry for entry in RankingStore(ranking_file).load().values() if entry.key not in seen
        )
    results = speedtest_and_save(
        entries,
        family="quotation",
        timeout=args.timeout,
        samples=max(1, int(getattr(args, "samples", 1) or 1)),
        max_workers=max(1, int(getattr(args, "workers", 16) or 16)),
        progress=True,
        ranking_file=ranking_file,
    )
    rows = []
    for r in results:
        rtt = f"{r.rtt_ms:.1f}ms" if r.rtt_ms is not None else "-"
        rows.append([f"{r.host}:{r.port}", "OK" if r.ok else "ERR", rtt, r.error or ""])
    _print_table(["host", "state", "rtt", "error"], rows)
    ok = sum(1 for r in results if r.ok)
    print(
        f"\n{ok}/{len(results)} 主站可达，已写入 {ranking_file}"
        "（quotation 族；其余四族见 atst hosts audit --family）"
    )
    return 0


def _age_text(seconds: float | None) -> str:
    """把观测时间差渲染成 ``12m`` / ``3h`` / ``5d``；未知返回 ``-``。

    负数按 0 处理：``observed_at`` 是本机写盘的时间戳，若系统时钟回拨（或排名文件
    来自时钟略快的另一台机器），``now - observed_at`` 会是负值，直接格式化会打印
    ``-5s`` 这种读起来像"未来观测"的年龄。
    """

    if seconds is None:
        return "-"
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 90 * 60:
        return f"{int(seconds / 60)}m"
    if seconds < 36 * 3600:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def _cmd_hosts_list(args: Any) -> int:
    """``atst hosts list``：查看当前生效的主站池（含排名文件合并结果）。

    只列 ``quotation``（STANDARD）族——排名文件只记这一族，所以这里就是"行情主站"
    的全貌。其余四族的候选池与巡检结论用 ``atst hosts audit --family``。

    末尾会打印排名文件的保鲜读数：条目数、文件更新时间、最老一条观测的年龄。排名
    条目没有自动过期语义，所以"该不该重扫"只能由这个读数来判断——超过 7 天就提示
    跑 ``hosts scan``（它会顺带淘汰不可达条目）。
    """
    from ..transport.hosts import POOL_BY_FAMILY, RankingStore, resolve_hosts

    ranking_file = str(getattr(args, "ranking_file", "~/.atst/server_ranking.json"))
    entries = resolve_hosts(None, family="quotation", ranking_file=ranking_file)
    in_pool = {entry.key for entry in POOL_BY_FAMILY.get("quotation", ())}
    now = time.time()

    rows = []
    for e in entries:
        rtt = f"{e.rtt_ms:.1f}ms" if e.rtt_ms is not None else "-"
        age = _age_text(None if e.observed_at is None else now - e.observed_at)
        rows.append(
            [
                e.key,
                e.family,
                rtt,
                age,
                "verified" if getattr(e, "verified", False) else "",
                "" if e.key in in_pool else "discovered",
            ]
        )
    _print_table(["host", "family", "rtt", "观测", "verified", "来源"], rows)

    extras = sum(1 for entry in entries if entry.key not in in_pool)
    print(
        f"\n共 {len(entries)} 台主站（quotation 族；其余四族见 atst hosts audit --family），"
        f"其中 {extras} 台来自发现（不在内置池）"
    )

    freshness = RankingStore(ranking_file).freshness()
    if freshness.entries:
        age = _age_text(freshness.age_seconds(now=now))
        print(
            f"排名文件：{freshness.entries} 条，更新于 {freshness.updated or '-'}，"
            f"最老观测 {age} 前"
        )
        oldest = freshness.age_seconds(now=now)
        if oldest is not None and oldest > 7 * 86400:
            print(
                "提示：排名已超过 7 天未刷新，运行 `atst hosts scan` 重新测速（会淘汰不可达条目）。"
            )
    else:
        print(
            f"排名文件：无（{ranking_file}）；运行 `atst hosts scan` 或 `atst hosts discover` 生成。"
        )
    return 0


def _cmd_hosts_discover(args: Any) -> int:
    """``atst hosts discover``：发现候选 → 实测 → 写排名（quotation 族）。

    与 ``hosts scan`` 的分工：scan 只重排**已知**候选，discover 才引入**新**成员。
    与 ``hosts audit --hosts-file`` 的分工：audit 会连内置池一起测，discover 只测本次
    发现的候选，因此它更适合按来源单独评估（"这份清单今天还有几台能用"）。
    """
    from ..errors import ConfigError
    from ..transport.discover import build_report, discover
    from ..transport.hosts import POOL_BY_FAMILY

    sources = tuple(getattr(args, "source", None) or ("tdx-install",))
    family = str(getattr(args, "family", "quotation"))
    quiet = bool(getattr(args, "quiet", False))
    dry_run = bool(getattr(args, "dry_run", False))
    verify = not bool(getattr(args, "no_verify", False))
    report_path = str(getattr(args, "report", "") or "")

    try:
        result = discover(
            sources,
            family=family,
            path=getattr(args, "path", None),
            url=getattr(args, "url", None),
            timeout=float(getattr(args, "timeout", 2.0)),
            samples=int(getattr(args, "samples", 1)),
            workers=int(getattr(args, "workers", 16)),
            ranking_file=str(getattr(args, "ranking_file", "~/.atst/server_ranking.json")),
            verify=verify,
            adopt=verify and not dry_run,
        )
    except (ConfigError, OSError, ValueError) as exc:
        print(f"发现失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    in_pool = {entry.key for entry in POOL_BY_FAMILY.get(family, ())}
    report = build_report(result, in_pool=in_pool)

    if report_path:
        with contextlib.suppress(OSError):
            path = Path(report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not quiet:
        print("== 发现来源 ==")
        for origin in result.origins:
            print(f"  {origin}")

    rows = []
    for row in report["hosts"]:
        rtt = f"{row['rtt_ms']:.1f}ms" if row["rtt_ms"] is not None else "-"
        rows.append(
            [
                f"{row['host']}:{row['port']}",
                "OK" if row["ok"] else "ERR",
                rtt,
                "new" if not row["in_pool"] else "",
                str(row["source"]),
                str(row["name"])[:24],
            ]
        )
    if rows and not quiet:
        _print_table(["host", "state", "rtt", "池", "来源", "名称"], rows)

    new_count = sum(1 for row in report["hosts"] if not row["in_pool"] and row["ok"])
    if not verify:
        print(f"\n候选 {report['total']} 台（未实测）；去掉 --no-verify 才会联网探测")
        return 0

    target = "已写入 " + str(getattr(args, "ranking_file", "~/.atst/server_ranking.json"))
    if family != "quotation":
        target = "未写入（排名文件只记 quotation 族）"
    elif dry_run:
        target = "未写入（--dry-run）"

    if not report["total"]:
        print(
            "未发现任何候选：本机没有找到 connect.cfg（可用 --path 指定），"
            "或清单里没有本协议栈支持的端口（7709/7719/7727/7615）。"
        )
        return 1

    print(
        f"\n候选 {report['total']} 台；可达 {report['reachable']} 台；"
        f"采纳 {report['adopted']} 台 {target}；"
        f"其中不在内置池且可达 {new_count} 台"
    )
    if not report["reachable"]:
        print("提示：全部候选不可达——先确认本机网络/代理，或换一个 --source。")
        return 1
    return 0


def _cmd_server_test(args: Any) -> int:
    """``atst server-test``：主站连通性测速。"""
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
    """启动 HTTP 行情网关（``create_runtime_app`` 的那 10 支业务路由）。

    WebSocket JSON-RPC 不在这张面上：它由
    :func:`atst.server.runtime_ws_server.serve_runtime_ws` 单独托管。
    ``--port 0`` 是合法值（OS 分配随机空闲端口），不得被 ``or 8000`` 短路。
    """
    from ..server.runtime_http import create_runtime_app

    try:
        import uvicorn
    except ImportError:  # pragma: no cover —— 依赖检查
        print("uvicorn 未安装: pip install atst[server]", file=sys.stderr)
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
    """反馈上报组：``atst feedback submit`` / ``atst feedback stats``。"""
    from ..errors import TdxError
    from ..feedback import FeedbackReporter, UserStats

    if args.feedback_command == "submit":
        reporter = FeedbackReporter(endpoint=args.endpoint, store_dir=args.store_dir)
        ok = reporter.report_error(TdxError(args.message), context={"source": "cli"})
        if ok:
            print("反馈已提交（已脱敏）。")
            return 0
        print(
            "反馈未发送：默认禁用。设置 ATST_FEEDBACK=1 启用，或 ATST_FEEDBACK=dry-run 调试预览。",
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
    """未知命令主动探测（薄壳接线 :mod:`atst.protocol.prober`）。"""
    from ..errors import TdxError
    from ..protocol.prober import Prober

    try:
        cmd_id = _parse_cmd_number(args.cmd)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("用法：atst probe 0x053e --market 1 --code 600519", file=sys.stderr)
        return 2

    rate = min(max(float(args.rate_limit), 0.1), 5.0)
    conn = _transport_kwargs(args)
    #: ``--archive-dir`` 不给（``None``）时直接透传：落地目录的默认值归
    #: :class:`~atst.protocol.prober.Prober` 自己所有（``DEFAULT_ARCHIVE_DIR``），
    #: 不在这里抄第二份。第 23 轮两处实测把这条钉住：改前 CLI 递 ``None`` 进
    #: ``Path(...)`` 当场 ``TypeError``，而 CLI 只接 :class:`TdxError`，于是文档那条
    #: ``atst probe 0x052D`` 示例报成空 context 的 E9000；改成 ``signature(Prober)``
    #: 反查默认值后，单元测试把 ``Prober`` 换成 ``**kwargs`` 桩类时又当场 ``KeyError``
    #: —— introspection 读的是"这一秒被绑到名字上的那个类"，默认值的所有者该是 callee。

    try:
        with family_client(get_client("stock", **conn)) as client:
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
            #: v2 起 ``Client`` 不再动态暴露 migrated capability（``dir(Client)``
            #: 即真实 API 面），CLI 的 rows 适配器是唯一还按能力名转发的地方。
            result = self._client.call(name, *args, **kwargs)
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
        print("用法：atst changes --types 8201,8193 [--page N] [--size N]", file=sys.stderr)
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
    """个股融资融券明细：``atst margin <symbol> [--days N]``（东财 Web 源）。"""
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
    """板块资金流排行：``atst sector-flow [--board industry] [--sort main_net]``。"""
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
    """复权 K 线：``atst adjusted-bars <symbol> [--method qfq|hfq|fixed|none]``。

    ``--provider`` 选的是**未复权原始 K 线**从哪来，``--event-source`` 选的是除权除息
    事件从哪来——两件事，别混。默认 ``eastmoney``（已对拍）；``tdx`` 走协议 ``0x000F``，
    其记录布局尚未由真机 golden 锁定，实测会被布局闸当场拒成 E1010。

    可选参数一律走 ``getattr`` + 默认值：处理器不应因调用方（内部复用、测试替身）少塞
    一个可选字段就整条挂掉。默认值与 :mod:`atst.cli.parser` 的 argparse 默认逐字对齐，
    由 ``tests/unit/test_cli_optional_defaults.py`` 钉住，防止两边漂移。
    """
    from ..domain.models import to_dicts

    method = getattr(args, "method", "qfq")
    event_source = getattr(args, "event_source", "eastmoney")
    kwargs: dict[str, Any] = {
        "method": method,
        "period": getattr(args, "period", "day"),
        "count": getattr(args, "count", 320),
        "start": getattr(args, "start", 0),
        "provider": getattr(args, "provider", "tdx"),
        "event_source": event_source,
    }
    anchor_date = getattr(args, "anchor_date", None)
    if anchor_date:
        kwargs["anchor_date"] = anchor_date
    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            data = to_dicts(api.adjusted_bars(args.symbol, **kwargs))
    except Exception as exc:  # noqa: BLE001 - 门面（local+tdx+web）异常统一出口
        print(f"错误：复权 K 线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {kwargs['period']} [{method}] 事件源={event_source} 共 {len(data)} 根")
    _print_bars_table(data)
    return 0


def _cmd_daily_enriched(args: Any) -> int:
    """宽表日线：``atst daily-enriched <symbol> [--adjust none|qfq|hfq]``。

    一行一交易日，24 列 = K 线 6 列 + 派生 6 列 + 估值 9 列 + 3 列来源标注。三条派生口径
    （``turnover`` 的分母、``vol_ratio`` 是日频近似、``is_st`` 是名称启发式）在
    :mod:`atst.domain.enrich` 里写死，这里只负责打印。

    可选参数同样走 ``getattr`` + 默认值，默认与 :mod:`atst.cli.parser` 对齐。
    """
    adjust = getattr(args, "adjust", "none")
    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            rows = api.daily_enriched(
                args.symbol,
                count=getattr(args, "count", 250),
                start=getattr(args, "start", 0),
                adjust=adjust,
                provider=getattr(args, "provider", "tdx"),
                event_source=getattr(args, "event_source", "eastmoney"),
            )
    except Exception as exc:  # noqa: BLE001 - 门面异常统一出口
        print(f"错误：宽表日线获取失败 —— {exc}", file=sys.stderr)
        return 2
    data = [dict(r) for r in rows]
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} 宽表日线 [adjust={adjust}] 共 {len(data)} 行")
    if not data:
        return 0
    headers = list(data[0].keys())
    table = [[_cell(row.get(h)) for h in headers] for row in data]
    _print_table(headers, table)
    return 0


def _cell(value: Any) -> Any:
    """宽表格子渲染：``None`` 打 ``-``，浮点保留 4 位，其余原样。

    ``None`` 在宽表里有确定含义（"这项没有值"），打空白会让人以为列错位。
    """
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return value


def _cmd_all_market(args: Any) -> int:
    """全市场行情摘要：``atst all-market [--node hs_a] [--source sina|tencent]``。

    ``--source`` 选的是**Provider**（``all_market`` 只登记了 sina / tencent 两条
    绑定），不是 :meth:`WebQuoteSession.all_market` 的关键字——那个签名里没有
    ``source``，把它当参数递交进去会被 ``validate_call`` 当场判 E1010。
    """
    from ..domain.models import to_dicts

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            data = to_dicts(
                api.all_market(
                    node=args.node,
                    page_size=args.page_size,
                    max_pages=args.max_pages,
                    provider=args.source,
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
    """分钟 K 线：``atst minute-klines <symbol> [--period 5min] [--count 240]``。"""
    from ..domain.models import to_dicts

    # 周期在入口收口：裸数字（--period 5）补成 5min，不认识的直接报可选值，
    # 不再让适配器里的裸 ValueError 穿成一句 E9000「未处理异常」。
    try:
        period = validate_period(args.period)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    try:
        with _ClientRows(**_client_kwargs(args)) as api:
            data = to_dicts(api.minute_klines(args.symbol, period=period, count=args.count))
    except Exception as exc:  # noqa: BLE001 - Web 源异常统一出口
        print(f"错误：分钟 K 线获取失败 —— {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {period} 共 {len(data)} 根")
    _print_bars_table(data)
    return 0


def _cmd_baidu(args: Any) -> int:
    """百度财经源：``atst baidu <symbol> [--kind kline|minute|ticks|quote]``。"""
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
    """东财基金源：``atst fund nav|estimate|list``。"""
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
    """东财指数成分股：``atst index constituents <code>``。"""
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
    """板块行情：``atst blocks <block_type> [--count N]``。"""
    try:
        with family_client(get_client("stock", **_transport_kwargs(args))) as c:
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
    """商品行情：``atst goods <symbol> [--kind quote|bars]``。"""
    try:
        with family_client(get_client("goods", **_transport_kwargs(args))) as c:
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
    """F10 资料：``atst f10 <symbol>`` 列栏目目录；``--file <name>`` 下载并解析正文。"""
    try:
        with family_client(get_client("f10", **_transport_kwargs(args))) as c:
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
    """代码表：``atst list <market> [--start N] [--count N]``（分页起始偏移）。"""
    from ..client import _PREFIX_MARKET

    market = args.market
    if market in ("sh", "sz", "bj"):
        market = _PREFIX_MARKET[market]
    try:
        with family_client(get_client("stock", **_transport_kwargs(args))) as c:
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


def _cmd_universe(args: Any) -> int:
    """标的清单：``atst universe <class> [--list-class]``。

    ``0x044D`` 死掉后（``atst list`` 实测 E3035）这里按类别给清单：代码表 →
    新浪节点 → tdx 段表探测三级降级，每个类别各走各的降级链。
    """
    from ..universe import (
        ASSET_CLASSES,
        SINA_NODES,
        class_of_directory,
        list_universe,
        universe_report,
    )

    if getattr(args, "list_class", False):
        # 这张表是"清单场景"的自述：类别 → 从哪拿 → 落到哪。``--json`` 给机器读，
        # 默认给眼睛读——两者出自同一份 ASSET_CLASSES，不各写一遍。
        if getattr(args, "json", False):
            print(
                json.dumps(
                    [
                        {
                            "kind": item.name,
                            "label": item.label,
                            "sina_node": item.sina_node,
                            "segments": list(item.tdx_segments),
                            "index_bit": item.index_bit,
                            "directory": item.directory,
                        }
                        for item in ASSET_CLASSES
                    ],
                    ensure_ascii=False,
                )
            )
            return 0
        for item in ASSET_CLASSES:
            node = item.sina_node or "（无节点，走段表探测）"
            print(
                f"{item.name:7s} {item.label}｜新浪节点={node}｜段={len(item.tdx_segments)}"
                f"｜指数位={item.index_bit}｜目录={item.directory}"
            )
        print("\n新浪行情中心节点（实测口径）：")
        for node, label in SINA_NODES:
            print(f"  {node:8s} {label}")
        return 0

    kind = args.kind
    if kind == "all":
        kinds: tuple[str, ...] = tuple(item.name for item in ASSET_CLASSES)
    else:
        kinds = (kind,)
    try:
        if args.dry_run:
            # probe=False 是 --dry-run 的全部意义：tdx 段表探测一次十几分钟，
            # 让它跑就等于把"先看代价"变成了"先付代价"。
            report = universe_report(
                kinds,
                source=args.source,
                root=args.root,
                limit=args.limit,
                workers=args.workers,
                probe=False,
            )
            if getattr(args, "json", False):
                # ``--json`` 是这一支的输出格式，不是"另一支命令"的开关：--dry-run
                # 下把它丢掉，等于让用户拿到一坨人读文本却以为拿到了 JSON。
                print(json.dumps(report, ensure_ascii=False))
                return 0
            for row in report:
                estimate = row.get("estimate")
                if row.get("error"):
                    tail = row["error"]
                elif estimate:
                    tail = f"约 {estimate} 候选待探测"
                else:
                    tail = f"{row['seconds']}s"
                print(
                    f"{row['kind']:7s} 源={row['source']:6s} 条数={row['count'] or '未知'}"
                    f"（{tail}）｜新浪节点={row['sina_node']}｜段={row['segments']}"
                )
            return 0
        rows = list_universe(
            kind,
            source=args.source,
            root=args.root,
            limit=args.limit,
            workers=args.workers,
        )
    except (LookupError, KeyError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                [{"code": r.code, "name": r.name, "kind": r.kind} for r in rows], ensure_ascii=False
            )
        )
        return 0
    print(f"# {kind} 共 {len(rows)} 条（源={args.source}）")
    _print_rows(
        [
            {
                "code": row.code,
                "name": row.name,
                "kind": row.kind,
                # 目录走 class_of_directory（与 --list-class、HTTP 面同一份表）：
                # 这里写 row.kind 是"抄了一遍当前恰好同名的事实"，改名时会漏。
                "directory": class_of_directory(row.kind),
            }
            for row in rows[: args.show]
        ]
    )
    if len(rows) > args.show:
        print(f"...（其余 {len(rows) - args.show} 条用 --json 取全量）")
    return 0


def _cmd_quotes_snapshot(args: Any) -> int:
    """批量快照：``atst quotes-snapshot <symbols...>``（全失败 → exit 1）。"""
    last_errors: list[tuple[str, BaseException]] = []
    try:
        with family_client(get_client("stock", **_transport_kwargs(args))) as c:
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
