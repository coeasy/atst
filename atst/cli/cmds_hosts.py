# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 运维与诊断命令：hosts / server-test / serve / feedback / probe。

P2-3（F-5/M7）：自 ``atst/cli/runtime_commands.py`` 按域拆出（v12 时代这一域就在
``cmds_hosts.py``）。本模块不再自写 ``print("错误：…") + return 2`` 出口——入口参数
错抛 :class:`~atst.errors.ValidationError`，内核/传输异常原样上抛，统一交
:func:`atst.cli.main` 的错误信封出口渲染（M7）。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..client import get_client
from ..client.api import Client
from ..errors import ValidationError
from ._common import _fmt, _print_table, _transport_kwargs, family_client


def _parse_cmd_number(text: str) -> int:
    """解析命令号：``0x053e`` / ``053e``（十六进制）/ ``1342``（十进制）。"""
    s = str(text).strip()
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s, 10)
    except ValueError as exc:
        raise ValueError(f"非法命令号: {text!r}（示例：0x053e 或 1342）") from exc


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
    from ..transport.discover import build_report, discover
    from ..transport.hosts import POOL_BY_FAMILY

    sources = tuple(getattr(args, "source", None) or ("tdx-install",))
    family = str(getattr(args, "family", "quotation"))
    quiet = bool(getattr(args, "quiet", False))
    dry_run = bool(getattr(args, "dry_run", False))
    verify = not bool(getattr(args, "no_verify", False))
    report_path = str(getattr(args, "report", "") or "")

    # P2-3（M7）：ConfigError / OSError / ValueError 不再就地 print+return 2，原样
    # 上抛交 :func:`atst.cli.main` 的错误信封出口——ConfigError 属 TdxError 族
    # （退出码 2），OSError/ValueError 走 E9000（退出码 1），与另外三面同一套退出码契约。
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
    """启动服务面：默认 HTTP 行情网关；``--ws`` 同时拉起 WebSocket JSON-RPC 面。

    P2-3（F-6/M8）：WS 面过去没有任何 CLI 入口，只能 ``python -m`` 或自写脚本调
    :func:`atst.server.runtime_ws_server.serve_runtime_ws`。``--ws`` 时两个面挂进
    同一个事件循环，且共用**同一个** :class:`~atst.client.api.Client`——一个进程里
    不允许有两个内核各自读配置。
    ``--port 0`` 是合法值（OS 分配随机空闲端口），不得被 ``or 8000`` 短路。
    """
    from ..errors import DependencyMissingError
    from ..server.runtime_http import create_runtime_app

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover —— 依赖检查
        # P2-3（M7）：依赖缺失是 TdxError 族错误，抛出去走信封出口（退出码仍 2）。
        raise DependencyMissingError("uvicorn 未安装: pip install atst[server]") from exc
    host = getattr(args, "bind", None) or "127.0.0.1"
    port_arg = getattr(args, "port", None)
    port = 8000 if port_arg is None else int(port_arg)
    if not getattr(args, "ws", False):
        app = create_runtime_app()
        uvicorn.run(app, host=host, port=port)
        return 0  # pragma: no cover —— uvicorn.run 阻塞至退出
    return _serve_http_and_ws(host=host, port=port)


def _serve_http_and_ws(*, host: str, port: int) -> int:
    """同时拉起 HTTP 与 WS 两个面（``atst serve --ws`` 的实现体）。

    同一份 ``Client`` 同时交给 HTTP 的 ASGI 工厂与 WS 的 JSON-RPC handler；WS 的
    per-connection 克隆（:func:`serve_runtime_ws`）共享的正是这一份。事件循环里
    uvicorn 的 ``Server.serve()`` 与 ``websockets`` 的 server 并存：HTTP 面由
    uvicorn 自己的信号处理收尾，WS 面随 ``async with`` 退出关闭，随后按所有权链关
    handler 与 Client——三者都归本函数所有，这两步 ``close()`` 是这条链唯一的落点。
    """
    import asyncio

    import uvicorn

    from ..server.runtime_http import create_runtime_app
    from ..server.runtime_ws import RuntimeJsonRpcHandler
    from ..server.runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

    client = Client()
    app = create_runtime_app(client=client)

    async def _run_both() -> None:
        handler = RuntimeJsonRpcHandler(client=client)
        ws_server = await serve_runtime_ws(handler=handler, config=RuntimeWsConfig(host=host))
        http_server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
        try:
            async with ws_server:
                await http_server.serve()
        finally:
            with contextlib.suppress(Exception):
                await handler.close()
            with contextlib.suppress(Exception):
                client.close()

    asyncio.run(_run_both())
    return 0  # pragma: no cover —— 服务循环阻塞至退出


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
    from ..protocol.prober import Prober

    # P2-3（M7）：命令号解析失败抛 ValidationError（E1010 / 退出码 2），用法示例
    # 收进消息随信封给出——main() 的信封出口替代本函数自写的那两行 stderr。
    try:
        cmd_id = _parse_cmd_number(args.cmd)
    except ValueError as exc:
        raise ValidationError(
            f"{exc}；用法：atst probe 0x053e --market 1 --code 600519",
            context={"phase": "wire_validation", "cmd": args.cmd},
        ) from exc

    rate = min(max(float(args.rate_limit), 0.1), 5.0)
    conn = _transport_kwargs(args)
    #: ``--archive-dir`` 不给（``None``）时直接透传：落地目录的默认值归
    #: :class:`~atst.protocol.prober.Prober` 自己所有（``DEFAULT_ARCHIVE_DIR``），
    #: 不在这里抄第二份。第 23 轮两处实测把这条钉住：改前 CLI 递 ``None`` 进
    #: ``Path(...)`` 当场 ``TypeError``，而 CLI 只接 :class:`TdxError`，于是文档那条
    #: ``atst probe 0x052D`` 示例报成空 context 的 E9000；改成 ``signature(Prober)``
    #: 反查默认值后，单元测试把 ``Prober`` 换成 ``**kwargs`` 桩类时又当场 ``KeyError``
    #: —— introspection 读的是"这一秒被绑到名字上的那个类"，默认值的所有者该是 callee。

    # P2-3（M7）：TdxError 不再就地 print+return 2，原样上抛交 main() 信封出口
    # （退出码同为 2，但走的是规范化信封，与另外三面同构）。
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
