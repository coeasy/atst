# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail closed when transport-pool family identity is invalid or inconsistent.

Normal clients arrive through ``resolve_hosts`` and already carry canonical
family identity. ``ConnectionPool`` and ``AsyncConnectionPool`` are public too,
so direct construction must enforce the same invariant instead of permitting a
STANDARD pool to contain F10/GOODS/MAC endpoints (or an unknown family string).
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import pool as _sync_impl
from .hosts import HostEntry

_SYNC_INIT = _sync_impl.ConnectionPool.__init__
_ASYNC_INIT = _async_impl.AsyncConnectionPool.__init__
_VALID_FAMILIES = frozenset(
    {Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10}
)
_MISSING = object()


def _validate_family_hosts(hosts: Any, *, family: Any) -> None:
    if family not in _VALID_FAMILIES:
        raise ConfigError(
            f"ConnectionPool family 非法: {family!r}",
            context={"family": family},
        )
    if hosts is _MISSING:
        return
    if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes, bytearray)):
        # Preserve the public contract explicitly rather than letting arbitrary
        # iterables be consumed differently by sync/async implementations.
        raise ConfigError(
            f"ConnectionPool hosts 必须是 HostEntry Sequence，收到 {type(hosts).__name__}"
        )
    for index, entry in enumerate(hosts):
        if not isinstance(entry, HostEntry):
            raise ConfigError(
                "ConnectionPool hosts 只接受 HostEntry: "
                f"index={index}, type={type(entry).__name__}"
            )
        if entry.family != family:
            raise ConfigError(
                "ConnectionPool host family 不匹配: "
                f"pool={family!r}, index={index}, host={entry.key}, family={entry.family!r}",
                context={
                    "pool_family": family,
                    "host_family": entry.family,
                    "host": entry.key,
                    "index": index,
                },
            )


@functools.wraps(
    _SYNC_INIT,
    assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
)
def _sync_init(self: _sync_impl.ConnectionPool, *args: Any, **kwargs: Any) -> None:
    hosts = args[0] if args else kwargs.get("hosts", _MISSING)
    family = kwargs.get("family", Family.STANDARD)
    _validate_family_hosts(hosts, family=family)
    _SYNC_INIT(self, *args, **kwargs)


@functools.wraps(
    _ASYNC_INIT,
    assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
)
def _async_init(self: _async_impl.AsyncConnectionPool, *args: Any, **kwargs: Any) -> None:
    hosts = args[0] if args else kwargs.get("hosts", _MISSING)
    family = kwargs.get("family", Family.STANDARD)
    _validate_family_hosts(hosts, family=family)
    _ASYNC_INIT(self, *args, **kwargs)


setattr(_sync_impl.ConnectionPool, "__init__", _sync_init)
setattr(_async_impl.AsyncConnectionPool, "__init__", _async_init)
