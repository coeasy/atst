# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 直连 market 命令：blocks / goods / f10 / list / universe / quotes-snapshot。

P2-3（F-5/M7）：自 ``atst/cli/runtime_commands.py`` 按域拆出（v12 时代这一域就在
``cmds_market.py``）。六支命令经 :func:`atst.client.get_client` 直连传输层、由
:func:`~atst.cli._common.family_client` 保护释放；异常不再就地 ``print`` + ``return 2``
（M7）——原样上抛交 :func:`atst.cli.main` 的错误信封出口，只有入口参数错
（未知类别 / 过期源名）转 :class:`~atst.errors.ValidationError`。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..client import get_client
from ..errors import ValidationError
from ._common import (
    _fmt,
    _pct,
    _print_rows,
    _print_table,
    _transport_kwargs,
    family_client,
)


def _cmd_blocks(args: Any) -> int:
    """板块行情：``atst blocks <block_type> [--count N]``。"""
    with family_client(get_client("stock", **_transport_kwargs(args))) as c:
        rows: list[dict[str, Any]] = []
        for start in range(0, args.count, 1000):
            page = c.block_quotes(args.block_type, start=start)
            rows.extend(page)
            if len(page) < 1000:
                break
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
    with family_client(get_client("goods", **_transport_kwargs(args))) as c:
        if args.kind == "quote":
            data = c.goods_quote(args.symbol, as_format="dict")
        else:
            data = c.goods_bars(args.symbol, period=args.period, count=args.count, as_format="dict")
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
    with family_client(get_client("f10", **_transport_kwargs(args))) as c:
        if args.file:
            sections = c.parse_text(c.download(args.symbol, args.file))
            data = [{"title": s.title, "text": s.text} for s in sections]
        else:
            data = c.catalog(args.symbol)
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
    with family_client(get_client("stock", **_transport_kwargs(args))) as c:
        rows: list[dict[str, Any]] = []
        for start in range(int(args.start), int(args.start) + int(args.count), 1000):
            page = c.security_list(int(market), start=start)
            rows.extend(page)
            if len(page) < 1000:
                break
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
    except LookupError as exc:
        # P2-3（M7）：未知类别 / 过期源名是入口参数错，转 ValidationError
        # （E1010 / 退出码 2）交信封出口；KeyError 是 LookupError 子类，一并接住。
        raise ValidationError(exc.args[0] if exc.args else str(exc)) from exc
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
    # P2-3（M7）：在线失败不再就地 print+return 2，原样上抛交 main() 信封出口；
    # 「全失败但零异常」的业务空转仍走下面的 exit 1（V5 契约，stderr 给摘要）。
    with family_client(get_client("stock", **_transport_kwargs(args))) as c:
        quotes = c.quotes_snapshot(args.symbols)
        last_errors = list(getattr(c, "last_errors", []))
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
