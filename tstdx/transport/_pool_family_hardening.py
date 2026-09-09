# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail closed on invalid pool identity, ownership and constructor settings.

Normal clients arrive through ``resolve_hosts`` and already carry canonical,
client-local HostEntry snapshots. ``ConnectionPool`` and ``AsyncConnectionPool``
are public too, so direct construction enforces the same identity/ownership
invariants and validates declared configuration instead of silently coercing or
clamping invalid values.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import pool as _sync_impl
from .hosts import HostEntry, parse_server

_SYNC_INIT = _sync_impl.ConnectionPool.__init__
_ASYNC_INIT = _async_impl.AsyncConnectionPool.__init__
_VALID_FAMILIES = frozenset(
    {Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10}
)
_MISSING = object()


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"ConnectionPool {name} 必须是 bool，收到 {value!r}")
    return value


def _require_int(
    name: str,
    value: Any,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"ConnectionPool {name} 必须是整数，收到 {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise ConfigError(f"ConnectionPool {name} 必须满足 {bound}，收到 {value}")
    return value


def _require_number(
    name: str,
    value: Any,
    *,
    positive: bool = False,
    allow_none: bool = False,
) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(f"ConnectionPool {name} 不能为 None")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"ConnectionPool {name} 必须是有限数值，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0):
        rule = "正有限数值" if positive else "有限数值"
        raise ConfigError(f"ConnectionPool {name} 必须是{rule}，收到 {value!r}")
    return normalized


def _validate_rate_limiter(value: Any, *, async_pool: bool) -> None:
    if value is None:
        return
    if not callable(getattr(value, "acquire", None)):
        raise ConfigError("ConnectionPool rate_limiter 缺少 callable acquire()")
    if async_pool:
        if not callable(getattr(value, "try_acquire", None)):
            raise ConfigError("AsyncConnectionPool rate_limiter 缺少 callable try_acquire()")
        if not isinstance(getattr(value, "strict", None), bool):
            raise ConfigError("AsyncConnectionPool rate_limiter.strict 必须是 bool")


def _validate_common_options(kwargs: dict[str, Any], *, async_pool: bool) -> None:
    _require_int("slots_per_host", kwargs.get("slots_per_host", 4), minimum=1)
    _require_number("timeout", kwargs.get("timeout", 3.0), positive=True)
    default_connect_timeout = None if async_pool else 2.0
    connect_timeout = kwargs.get("connect_timeout", default_connect_timeout)
    _require_number(
        "connect_timeout",
        connect_timeout,
        positive=True,
        allow_none=True,
    )

    heartbeat_interval = kwargs.get("heartbeat_interval", 30)
    if heartbeat_interval is not None:
        _require_int("heartbeat_interval", heartbeat_interval, minimum=0)
    _require_int(
        "heartbeat_cmd",
        kwargs.get("heartbeat_cmd", _sync_impl.DEFAULT_HEARTBEAT_CMD),
        minimum=0,
        maximum=0xFFFF,
    )
    _require_int("max_retries", kwargs.get("max_retries", 3), minimum=0)
    _require_bool("use_tls", kwargs.get("use_tls", False))
    handshake = kwargs.get("handshake", None)
    if handshake is not None:
        _require_bool("handshake", handshake)
    handshake_strict = _require_bool(
        "handshake_strict",
        kwargs.get("handshake_strict", False),
    )
    if handshake is False and handshake_strict:
        raise ConfigError(
            "ConnectionPool handshake=False 时 handshake_strict=True 无效；"
            "请启用握手或关闭 strict"
        )
    _validate_rate_limiter(kwargs.get("rate_limiter", None), async_pool=async_pool)


def _validate_sync_only_options(kwargs: dict[str, Any]) -> None:
    _require_bool("keepalive", kwargs.get("keepalive", True))
    _require_int("speedtest_threshold", kwargs.get("speedtest_threshold", 3), minimum=1)
    _require_number("idle_timeout", kwargs.get("idle_timeout", 300.0))
    callback = kwargs.get("on_host_down", None)
    if callback is not None and not callable(callback):
        raise ConfigError(
            "ConnectionPool on_host_down 必须是 callable 或 None，"
            f"收到 {type(callback).__name__}"
        )


def _canonical_family_hosts(hosts: Any, *, family: Any) -> Any:
    if family not in _VALID_FAMILIES:
        raise ConfigError(
            f"ConnectionPool family 非法: {family!r}",
            context={"family": family},
        )
    if hosts is _MISSING:
        return _MISSING
    if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes, bytearray)):
        raise ConfigError(
            f"ConnectionPool hosts 必须是 HostEntry Sequence，收到 {type(hosts).__name__}"
        )
    if not hosts:
        raise ConfigError("ConnectionPool hosts 不能为空")

    canonical: list[HostEntry] = []
    seen: set[str] = set()
    for index, entry in enumerate(hosts):
        if not isinstance(entry, HostEntry):
            raise ConfigError(
                "ConnectionPool hosts 只接受 HostEntry: "
                f"index={index}, type={type(entry).__name__}"
            )
        normalized = parse_server(entry, family=family)
        if normalized.family != family:
            raise ConfigError(
                "ConnectionPool host family 不匹配: "
                f"pool={family!r}, index={index}, host={normalized.key}, "
                f"family={normalized.family!r}",
                context={
                    "pool_family": family,
                    "host_family": normalized.family,
                    "host": normalized.key,
                    "index": index,
                },
            )
        if normalized.key in seen:
            raise ConfigError(
                f"ConnectionPool hosts 存在重复 canonical endpoint: {normalized.key}",
                context={"host": normalized.key, "index": index},
            )
        seen.add(normalized.key)
        canonical.append(normalized)
    return canonical


def _replace_hosts_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    canonical_hosts: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if canonical_hosts is _MISSING:
        return args, kwargs
    if args:
        return (canonical_hosts, *args[1:]), kwargs
    updated = dict(kwargs)
    updated["hosts"] = canonical_hosts
    return args, updated


@functools.wraps(
    _SYNC_INIT,
    assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
)
def _sync_init(self: _sync_impl.ConnectionPool, *args: Any, **kwargs: Any) -> None:
    hosts = args[0] if args else kwargs.get("hosts", _MISSING)
    family = kwargs.get("family", Family.STANDARD)
    canonical = _canonical_family_hosts(hosts, family=family)
    _validate_common_options(kwargs, async_pool=False)
    _validate_sync_only_options(kwargs)
    args, kwargs = _replace_hosts_argument(args, kwargs, canonical)
    _SYNC_INIT(self, *args, **kwargs)


@functools.wraps(
    _ASYNC_INIT,
    assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
)
def _async_init(self: _async_impl.AsyncConnectionPool, *args: Any, **kwargs: Any) -> None:
    hosts = args[0] if args else kwargs.get("hosts", _MISSING)
    family = kwargs.get("family", Family.STANDARD)
    canonical = _canonical_family_hosts(hosts, family=family)
    _validate_common_options(kwargs, async_pool=True)
    args, kwargs = _replace_hosts_argument(args, kwargs, canonical)
    _ASYNC_INIT(self, *args, **kwargs)


setattr(_sync_impl.ConnectionPool, "__init__", _sync_init)
setattr(_async_impl.AsyncConnectionPool, "__init__", _async_init)
