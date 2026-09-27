# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Fail-closed configuration contract helpers.

All ``*hardening*.py`` files have been merged back here. These helpers are used
by the canonical constructors of :class:`TcpConnection`,
:class:`AsyncTcpConnection`, :class:`ConnectionPool`,
:class:`AsyncConnectionPool`, and :func:`resolve_hosts`.
"""

from __future__ import annotations

import math
import ssl
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..errors import ConfigError
from ..protocol.commands import Family

if TYPE_CHECKING:
    from .hosts import HostEntry

__all__ = [
    "validate_common_connection_args",
    "validate_common_pool_options",
    "validate_sync_pool_only_options",
    "canonical_family_hosts",
    "require_bool",
    "require_timeout",
]


# --------------------------------------------------------------------------- #
# Scalar validators                                                           #
# --------------------------------------------------------------------------- #


def require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} 必须是 bool，收到 {value!r}")
    return value


def require_timeout(name: str, value: Any, *, allow_none: bool = False) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(f"{name} 不能为 None")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是正有限数值，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ConfigError(f"{name} 必须是正有限数值，收到 {value!r}")
    return normalized


def require_slot_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"slot_id 必须是非负整数，收到 {value!r}")
    return value


def require_int(
    name: str,
    value: Any,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} 必须是整数，收到 {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise ConfigError(f"{name} 必须满足 {bound}，收到 {value}")
    return value


def require_number(
    name: str,
    value: Any,
    *,
    positive: bool = False,
    allow_none: bool = False,
) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(f"{name} 不能为 None")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是有限数值，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0):
        rule = "正有限数值" if positive else "有限数值"
        raise ConfigError(f"{name} 必须是{rule}，收到 {value!r}")
    return normalized


def validate_optional_bytes(name: str, value: Any) -> bytes | None:
    if value is not None and not isinstance(value, bytes):
        raise ConfigError(f"{name} 必须是 bytes 或 None，收到 {type(value).__name__}")
    return value


def validate_rate_limiter(value: Any, *, async_pool: bool) -> None:
    if value is None:
        return
    if not callable(getattr(value, "acquire", None)):
        raise ConfigError("rate_limiter 缺少 callable acquire()")
    if async_pool:
        if not callable(getattr(value, "try_acquire", None)):
            raise ConfigError("AsyncConnectionPool rate_limiter 缺少 callable try_acquire()")
        if not isinstance(getattr(value, "strict", None), bool):
            raise ConfigError("AsyncConnectionPool rate_limiter.strict 必须是 bool")


# --------------------------------------------------------------------------- #
# Connection-level validation (TcpConnection / AsyncTcpConnection)            #
# --------------------------------------------------------------------------- #


def validate_common_connection_args(
    *,
    host: Any,
    port: Any,
    family: Any,
    timeout: Any,
    connect_timeout: Any,
    use_tls: Any,
    tls_context: Any,
    slot_id: Any,
    handshake: Any,
    handshake_strict: Any,
    handshake_blob: Any,
    keepalive: Any = None,
) -> tuple[str, int, str, float, float | None, bool, int, bool, bool, bytes | None]:
    """Validate and canonicalize the full shared constructor surface of both
    sync and async direct :class:`TcpConnection` / :class:`AsyncTcpConnection`.

    ``keepalive`` is sync-only so it is optional here. The caller passes it
    only when sync construction needs it; async constructors omit it.
    """

    from .hosts import parse_server

    entry = parse_server((host, port), family=family)
    request_timeout = require_timeout("timeout", timeout)
    assert request_timeout is not None
    connect = require_timeout("connect_timeout", connect_timeout, allow_none=True)
    tls_enabled = require_bool("use_tls", use_tls)
    slot = require_slot_id(slot_id)
    handshake_enabled = require_bool("handshake", handshake)
    strict = require_bool("handshake_strict", handshake_strict)
    blob = validate_optional_bytes("handshake_blob", handshake_blob)

    if keepalive is not None:
        require_bool("keepalive", keepalive)

    if tls_context is not None and not isinstance(tls_context, ssl.SSLContext):
        raise ConfigError(
            f"tls_context 必须是 ssl.SSLContext 或 None，收到 {type(tls_context).__name__}"
        )
    if tls_context is not None and not tls_enabled:
        raise ConfigError("use_tls=False 时 tls_context 不会生效；请启用 TLS 或移除 tls_context")
    if not handshake_enabled and strict:
        raise ConfigError("handshake=False 时 handshake_strict=True 无效")
    if not handshake_enabled and blob is not None:
        raise ConfigError("handshake=False 时 handshake_blob 不会生效")

    return (
        entry.host,
        entry.port,
        entry.family,
        request_timeout,
        connect,
        tls_enabled,
        slot,
        handshake_enabled,
        strict,
        blob,
    )


# --------------------------------------------------------------------------- #
# Pool-level validation (ConnectionPool / AsyncConnectionPool)               #
# --------------------------------------------------------------------------- #


_VALID_POOL_FAMILIES = frozenset(
    {Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10}
)
_MISSING = object()


def validate_common_pool_options(kwargs: dict[str, Any], *, async_pool: bool) -> None:
    """Validate options shared by sync and async pool constructors.

    This only checks option values; it does not populate defaults — the
    constructor body itself assigns those. ``DEFAULT_HEARTBEAT_CMD`` must be
    supplied by the caller since sync and async pools may diverge.
    """

    from .pool import DEFAULT_HEARTBEAT_CMD as _sync_cmd

    heartbeat_cmd = kwargs.get("heartbeat_cmd", _sync_cmd)
    _require_int = require_int  # bind for brevity in local defs below

    require_int("slots_per_host", kwargs.get("slots_per_host", 4), minimum=1)
    require_number("timeout", kwargs.get("timeout", 3.0), positive=True)
    default_connect_timeout = None if async_pool else 2.0
    connect_timeout = kwargs.get("connect_timeout", default_connect_timeout)
    require_number(
        "connect_timeout",
        connect_timeout,
        positive=True,
        allow_none=True,
    )

    heartbeat_interval = kwargs.get("heartbeat_interval", 30)
    if heartbeat_interval is not None:
        require_int("heartbeat_interval", heartbeat_interval, minimum=0)
    require_int("heartbeat_cmd", heartbeat_cmd, minimum=0, maximum=0xFFFF)
    require_int("max_retries", kwargs.get("max_retries", 3), minimum=0)
    require_bool("use_tls", kwargs.get("use_tls", False))
    handshake = kwargs.get("handshake")
    if handshake is not None:
        require_bool("handshake", handshake)
    handshake_strict = require_bool(
        "handshake_strict",
        kwargs.get("handshake_strict", False),
    )
    if handshake is False and handshake_strict:
        raise ConfigError(
            "ConnectionPool handshake=False 时 handshake_strict=True 无效；请启用握手或关闭 strict"
        )
    validate_rate_limiter(kwargs.get("rate_limiter"), async_pool=async_pool)
    require_number("idle_timeout", kwargs.get("idle_timeout", 300.0))


def validate_sync_pool_only_options(kwargs: dict[str, Any]) -> None:
    require_bool("keepalive", kwargs.get("keepalive", True))
    require_int("speedtest_threshold", kwargs.get("speedtest_threshold", 3), minimum=1)
    callback = kwargs.get("on_host_down")
    if callback is not None and not callable(callback):
        raise ConfigError(
            f"ConnectionPool on_host_down 必须是 callable 或 None，收到 {type(callback).__name__}"
        )


def pool_owned_host(entry: HostEntry) -> HostEntry:
    """Start one pool generation with identity/probe evidence only."""

    from .hosts import HostEntry

    return HostEntry(
        host=entry.host,
        port=entry.port,
        family=entry.family,
        name=entry.name,
        verified=entry.verified,
        connect_ms=entry.connect_ms,
        rtt_ms=entry.rtt_ms,
    )


def canonical_family_hosts(hosts: Any, *, family: Any) -> Any:
    """Validate host identity + family match, and return pool-owned snapshots.

    Returns ``_MISSING`` (a module-level sentinel) when the caller did not
    supply hosts at all, so callers can tell "missing because caller omitted"
    from "missing because we filtered everything out".
    """

    from .hosts import HostEntry, parse_server

    if family not in _VALID_POOL_FAMILIES:
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
                f"ConnectionPool hosts 只接受 HostEntry: index={index}, type={type(entry).__name__}"
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
        canonical.append(pool_owned_host(normalized))
    return canonical
