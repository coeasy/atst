# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 内核数据命令（v13 ``cmd_*`` 一族）。

P2-3（F-5/M7）：自 ``atst/cli/runtime_commands.py`` 按域拆出。本模块只放"经唯一入口
:class:`~atst.client.api.Client` 取数"的数据命令：选择器透传、结果信封序列化与流式
订阅。参数校验失败抛 :class:`~atst.errors.ValidationError`，内核异常原样上抛——两族
都由 :func:`atst.cli.main` 的错误信封出口统一渲染，本模块不再自写 stderr 出口。
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
from ..runtime.orchestration import FallbackPolicy
from ..server.serialization import jsonable, serialize_result
from ..server.wire_fields import parse_query_call
from ._common import _client_kwargs, _pct


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
    # P2-6/L1：query 入参形状校验收口到 parse_query_call（与 HTTP/WS/MCP 同一函数、
    # 同一套文案）；JSON 解码失败那条 CLI 特有的报错留在上面，不归收口管。
    capability, call_args, call_kwargs = parse_query_call(
        face="cli_query",
        where="atst query 的 --kwargs",
        capability=args.capability,
        args=call_args,
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
