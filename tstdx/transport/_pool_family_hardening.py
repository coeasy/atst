# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail closed on invalid pool family identity and caller-owned host state.

Normal clients arrive through ``resolve_hosts`` and already carry canonical,
client-local HostEntry snapshots. ``ConnectionPool`` and ``AsyncConnectionPool``
are public too, so direct construction must enforce the same invariant instead
of permitting unknown families, cross-family endpoints, duplicate canonical
endpoints, non-canonical host/port values, or mutable HostEntry objects shared
with caller code.
"""

from __future__ import annotations

import functools
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


def _canonical_family_hosts(hosts: Any, *, family: Any) -> Any:
    if family not in _VALID_FAMILIES:
        raise ConfigError(
            f"ConnectionPool family 非法: {family!r}",
            context={"family": family},
        )
    if hosts is _MISSING:
        return _MISSING
    if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes, bytearray)):
        # Preserve the public contract explicitly rather than letting arbitrary
        # iterables be consumed differently by sync/async implementations.
        raise ConfigError(
            f"ConnectionPool hosts 必须是 HostEntry Sequence，收到 {type(hosts).__name__}"
        )

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
        # parse_server(HostEntry) returns a validated fresh dataclass snapshot.
        # The pool owns this object; later caller mutations cannot alter live
        # health/circuit/probe state inside the pool generation.
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
    args, kwargs = _replace_hosts_argument(args, kwargs, canonical)
    _ASYNC_INIT(self, *args, **kwargs)


setattr(_sync_impl.ConnectionPool, "__init__", _sync_init)
setattr(_async_impl.AsyncConnectionPool, "__init__", _async_init)
