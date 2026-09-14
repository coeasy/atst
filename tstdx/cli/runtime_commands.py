# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 CLI command handlers backed exclusively by Client."""

from __future__ import annotations

import json
import time
from typing import Any

from .. import __version__
from ..client_api import Client
from ..errors import ValidationError
from ..integration.serialization import jsonable, serialize_result
from ..orchestration import FallbackPolicy


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
    with Client() as client:
        _print(serialize_result(client.call(
            args.capability,
            *call_args,
            provider=args.provider,
            channel=args.channel,
            currentness=args.currentness,
            max_age=args.max_age,
            use_cache=not args.no_cache,
            **call_kwargs,
        )))
    return 0


def cmd_quotes(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.quotes(args.symbols, provider=args.provider, policy=_policy(args.fallback), max_age=args.max_age, use_cache=not args.no_cache)))
    return 0


def cmd_bars(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.bars(args.symbol, provider=args.provider, policy=_policy(args.fallback), period=args.period, count=args.count, start=args.start, adjustment=args.adjustment, max_age=args.max_age, use_cache=not args.no_cache)))
    return 0


def cmd_snapshot(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.snapshot(args.symbol, provider=args.provider, use_cache=not args.no_cache)))
    return 0


def cmd_minute(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.minute(args.symbol, provider=args.provider, use_cache=not args.no_cache)))
    return 0


def cmd_trades(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.trades(args.symbol, provider=args.provider, start=args.start, count=args.count, use_cache=not args.no_cache)))
    return 0


def cmd_security_count(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.security_count(market=args.market, provider=args.provider, use_cache=not args.no_cache)))
    return 0


def cmd_security_list(args: Any) -> int:
    with Client() as client:
        _print(serialize_result(client.security_list(market=args.market, start=args.start, provider=args.provider, use_cache=not args.no_cache)))
    return 0


def cmd_stream(args: Any) -> int:
    def _quote(symbol: str, quote: dict[str, Any]) -> None:
        _print({"symbol": symbol, "quote": quote})

    def _error(exc: Exception) -> None:
        _print({"stream_error": type(exc).__name__})

    with Client() as client:
        stream = client.stream(args.symbols, provider=args.provider, interval=args.interval, diff_only=args.diff_only, max_queue=args.max_queue, on_quote=_quote, on_error=_error)
        stream.start()
        try:
            if args.seconds > 0:
                time.sleep(args.seconds)
            else:
                while True:
                    time.sleep(3600)
        finally:
            stream.stop()
    return 0
