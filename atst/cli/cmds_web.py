# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI Web 源命令（v12 ``cmds_web.py`` 迁移来的门面薄壳）。

P2-3（F-5/M7）：自 ``atst/cli/runtime_commands.py`` 按域拆出。执行入口只有内核一个
（``_ClientRows`` → ``Client.call``）；Web 源异常不再就地 ``print`` + ``return 2``
（M7）——内核抛什么本模块就抛什么，由 :func:`atst.cli.main` 的错误信封出口统一渲染；
只有入口参数错（``--types`` 非法 / 周期不认识 / 缺 ``code``）才就地转
:class:`~atst.errors.ValidationError`。
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ValidationError
from ._common import (
    _client_kwargs,
    _fmt,
    _print_bars_table,
    _print_rows,
    _print_table,
    validate_period,
)


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
    # P2-3（M7）：入口参数错就地转 ValidationError（E1010 / 退出码 2），用法示例
    # 收进消息随信封给出；内核异常原样上抛，不再自写 stderr 出口。
    try:
        types = [int(t) for t in str(args.types).split(",") if t.strip()]
    except ValueError as exc:
        raise ValidationError(
            f"--types 必须是逗号分隔整数 —— {exc}"
            "；用法：atst changes --types 8201,8193 [--page N] [--size N]",
            context={"phase": "wire_validation", "types": args.types},
        ) from exc
    with _ClientRows(**_client_kwargs(args)) as api:
        rows = api.stock_changes(types, page=args.page, size=args.size)
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
    with _ClientRows(**_client_kwargs(args)) as api:
        rows = api.hot_rank(page=args.page, size=args.size)
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

    with _ClientRows(**_client_kwargs(args)) as api:
        rows = to_dicts(api.margin(args.symbol, days=args.days))
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

    with _ClientRows(**_client_kwargs(args)) as api:
        rows = to_dicts(api.sector_flow(args.board, sort=args.sort, limit=args.limit))
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
    with _ClientRows(**_client_kwargs(args)) as api:
        data = to_dicts(api.adjusted_bars(args.symbol, **kwargs))
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
    with _ClientRows(**_client_kwargs(args)) as api:
        rows = api.daily_enriched(
            args.symbol,
            count=getattr(args, "count", 250),
            start=getattr(args, "start", 0),
            adjust=adjust,
            provider=getattr(args, "provider", "tdx"),
            event_source=getattr(args, "event_source", "eastmoney"),
        )
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

    with _ClientRows(**_client_kwargs(args)) as api:
        data = to_dicts(
            api.all_market(
                node=args.node,
                page_size=args.page_size,
                max_pages=args.max_pages,
                provider=args.source,
            )
        )
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.node}（{args.source}）共 {len(data)} 条")
    _print_rows(data)
    return 0


def _cmd_minute_klines(args: Any) -> int:
    """分钟 K 线：``atst minute-klines <symbol> [--period 5min] [--count 240]``。"""
    from ..domain.models import to_dicts

    # 周期在入口收口（P2-3/M7）：裸数字（--period 5）补成 5min；不认识的抛
    # ValidationError（E1010 / 退出码 2）交信封出口。内核异常原样上抛。
    try:
        period = validate_period(args.period)
    except ValueError as exc:
        raise ValidationError(str(exc), context={"phase": "wire_validation"}) from exc

    with _ClientRows(**_client_kwargs(args)) as api:
        data = to_dicts(api.minute_klines(args.symbol, period=period, count=args.count))
    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str))
        return 0
    print(f"# {args.symbol} {period} 共 {len(data)} 根")
    _print_bars_table(data)
    return 0


def _cmd_baidu(args: Any) -> int:
    """百度财经源：``atst baidu <symbol> [--kind kline|minute|ticks|quote]``。"""
    from ..domain.models import to_dicts

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
        # P2-3（M7）：缺必填 code 是入口参数错，抛 ValidationError（E1010 / 退出码 2）。
        raise ValidationError(f"fund {args.action} 需要 <code>（6 位基金代码）")
    with _ClientRows(**_client_kwargs(args)) as api:
        if args.action == "nav":
            rows = api.fund_nav_history(
                args.code, page_size=args.page_size, page_index=args.page_index
            )
        elif args.action == "estimate":
            rows = [api.fund_estimate(args.code)]
        else:
            rows = api.fund_list()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# 基金 {args.action} 共 {len(rows)} 条")
    _print_rows(rows)
    return 0


def _cmd_index(args: Any) -> int:
    """东财指数成分股：``atst index constituents <code>``。"""
    if args.action == "constituents" and not args.code:
        # P2-3（M7）：缺必填 code 是入口参数错，抛 ValidationError（E1010 / 退出码 2）。
        raise ValidationError(
            "index constituents 需要 <code>（指数代码，如 000300 / 000905 / 930050）"
        )
    with _ClientRows(**_client_kwargs(args)) as api:
        rows = api.index_constituents(args.code)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, default=str))
        return 0
    print(f"# 指数 {args.code} 成分股共 {len(rows)} 条")
    _print_rows(rows)
    return 0
