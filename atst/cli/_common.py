# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 公共工具：表格输出、主站参数解析与直连家族客户端的保护区。

模块级只 import 标准库；伸向包内的 import 无一例外写在函数体内（延迟导入），
这样本模块仍可被裸环境脚本直接引入。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Protocol, TypeVar

__all__ = [
    "_client_kwargs",
    "_fmt",
    "_pct",
    "_print_bars_table",
    "_print_rows",
    "_print_table",
    "_resolve_hosts",
    "_transport_kwargs",
    "_transport_timeout",
    "family_client",
]


def _print_table(headers: list[str], rows: list[list[Any]]) -> None:
    cols = [str(h) for h in headers]
    widths = [len(c) for c in cols]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def _fmt(v: Any, nd: int = 2) -> str:
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def _pct(q: Mapping[str, Any]) -> float:
    """由 price / last_close 计算涨跌幅（Quote.to_dict 不含该字段）。"""
    pct = q.get("pct_change")
    if pct is not None:
        return float(pct)
    lc = q.get("last_close") or 0
    price = q.get("price") or 0
    if lc:
        return (price - lc) / lc * 100
    return 0.0


#: K 线周期白名单。适配器（东财/新浪/腾讯）只认 ``1min``/``5min``/.../``day``
#: 这种带单位串，裸数字（``--period 5``）在链路深处会变成
#: ``ValueError: 未知 K 线周期 '5'``——一层没接住的裸异常，到调用方只剩一句
#: ``E9000 未处理异常``。于是周期口径在**入口**收一次：裸数字补上 ``min``，
#: 真正不认识的在入口就报清楚可选值，不再让裸 ValueError 往下游穿。
KLINE_PERIODS: tuple[str, ...] = ("1min", "5min", "15min", "30min", "60min", "day")
_KLINE_PERIOD_ALIASES: dict[str, str] = {
    "1": "1min",
    "5": "5min",
    "15": "15min",
    "30": "30min",
    "60": "60min",
    "d": "day",
    "1d": "day",
    "1day": "day",
}


def normalize_period(value: str) -> str:
    """把 CLI 传进来的周期归一化成适配器的口径；不认识的原样返回。

    ``"5"`` → ``"5min"``、``"d"``/``"1d"``/``"1day"`` → ``"day"``；
    ``"quarterly"`` 这类未知值原样返回，由调用方自己报错——这里不替调用方
    决定「该不该放行」。
    """
    token = str(value).strip()
    if token in KLINE_PERIODS:
        return token
    return _KLINE_PERIOD_ALIASES.get(token.lower(), token)


def validate_period(value: str) -> str:
    """归一化后校验：不在白名单里就抛 :class:`ValueError`（调用方负责打印）。"""
    normalized = normalize_period(value)
    if normalized not in KLINE_PERIODS:
        raise ValueError(
            f"未知 K 线周期 {value!r}；可选: {'/'.join(KLINE_PERIODS)}"
            "（裸数字 1/5/15/30/60 就是对应分钟周期）"
        )
    return normalized


def _resolve_hosts(args: argparse.Namespace):
    from ..transport.hosts import parse_server

    hosts = getattr(args, "host", None)
    if hosts:
        return [parse_server(h) for h in hosts]
    return None


def _client_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Kernel path: forward only what the caller actually said.

    ``UnifiedRuntime`` is the single reader of the config surface, so an
    unset ``--host`` / ``--timeout`` must stay ``None`` here; substituting a
    CLI-side default would silently outrank ``atst.toml``.
    """
    return {"hosts": _resolve_hosts(args), "timeout": getattr(args, "timeout", None)}


def _transport_timeout(args: argparse.Namespace) -> float:
    """Timeout for a command that builds its client outside the kernel.

    ``[core] timeout`` is the documented default; an explicit ``--timeout``
    still wins. The kernel applies the same rule to the family clients it
    builds, so the CLI must not substitute its own literal default here.
    """
    from ..config import get_config

    timeout = getattr(args, "timeout", None)
    return float(timeout) if timeout is not None else get_config().core.timeout


def _transport_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    """Connection kwargs for raw **TDX-family** commands (``probe`` / ``blocks`` / ``goods`` / ``f10`` …).

    These build a transport client outside the kernel, so nothing else applies
    the config surface for them.  That is exactly why they go through
    :func:`~atst.transport.pool.pool_settings_from_config` — the same single
    config→transport translation the kernel's executor uses.  Hand-listing two
    of the six keys here (第 31 轮之前的形状) made one TOML key mean one thing on
    the quotes path and another thing on the CLI path: ``[hosts]
    slots_per_host``、``[core] max_retries``、``[core] heartbeat_interval``、
    ``[rate_limit]``、``[security] use_tls`` 在这六支命令上当场蒸发。
    显式 ``--timeout`` 仍然赢过配置值，与内核给跳超时同一条口径。
    """
    from ..config import get_config
    from ..transport.pool import pool_settings_from_config

    cfg = get_config()
    settings = pool_settings_from_config(cfg)
    hosts = _resolve_hosts(args)
    settings["hosts"] = hosts if hosts is not None else (list(cfg.hosts.servers) or None)
    settings["timeout"] = _transport_timeout(args)
    return settings


class _Closable(Protocol):
    def close(self) -> None: ...


_C = TypeVar("_C", bound=_Closable)


@contextmanager
def family_client(client: _C) -> Iterator[_C]:
    """CLI 直连家族客户端唯一那道保护区：交进来就接管，块体怎么下场都 ``close()``。

    与执行器的
    :meth:`~atst.runtime.executor.DirectProviderExecutor._client_session` 同一条口径
    （第 31 轮 31-B4 → 31-C3 → 本条），连形状也一样：**构造在调用点，保护区只管释放**。
    入参写成对象而不是 ``kind: str``，是为了让 ``with family_client(get_client("goods", …))
    as c`` 之后的 ``c`` 仍是 ``GoodsClient``——把 kind 传进保护区再查注册表，返回类型就塌成
    ``Any``，工厂那五道 ``@overload`` 在 CLI 面等于白设（``c.block_quotes`` 一类家族专有方法
    不再被 mypy 看见）。风险只在 ``__enter__`` 落在哪里：``get_client`` 交回来的对象**已经**
    带着连接池与心跳线程，``with client`` 还要再走一次 ``client.__enter__()``（即 ``open()``），
    而那一步不归 try 管。今天 ``open()`` 在 ``bestip=False`` 下只 ``return self``，所以这六处
    并非当场在漏；但一个叫"建连"的方法哪天真握手一次，六处就同时变成六个漏点。判据：
    ``tests/architecture/test_client_family_transport.py`` 的第 5 件事——包内任何 ``with``
    直接包住家族构造出口（``get_client(...)`` / 家族类 / ``_pool_client(...)``）即为红。
    """
    try:
        yield client
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# 通用：按首行键生成表格
# --------------------------------------------------------------------------- #
def _print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(无数据)")
        return
    # 优先稳定顺序：常用键在前，其余按出现顺序
    preferred = [
        "code",
        "name",
        "market",
        "type",
        "time",
        "price",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "avg_price",
        "bid",
        "ask",
        "buyorsell",
        "num",
        "datetime",
    ]
    keys: list[str] = []
    for k in preferred:
        if k in rows[0]:
            keys.append(k)
    for k in rows[0]:
        if k not in keys:
            keys.append(k)
    table = [[r.get(k, "") for k in keys] for r in rows]
    _print_table(keys, table)


def _print_bars_table(data: list[dict[str, Any]]) -> None:
    """K 线表（Bar.to_dict 后的 dict 行）统一排版。"""
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
