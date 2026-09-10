# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Harden public pool factory selector semantics.

``ConnectionPool.from_config(..., hosts=...)`` historically used truthiness to
decide whether an explicit host override was present.  That made ``hosts=[]``
look identical to ``hosts=None`` and silently fell back to configured/default
network endpoints.  Explicit selectors are authoritative throughout v12, so an
empty override must fail closed; only ``None`` means "use configuration".
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import pool as _sync_impl
from .hosts import HostEntry
from .ratelimit import SessionRateLimiter

_BASE_FROM_CONFIG = _sync_impl.ConnectionPool.from_config.__func__


@functools.wraps(
    _BASE_FROM_CONFIG,
    assigned=("__name__", "__qualname__", "__doc__", "__annotations__"),
)
def _from_config(
    cls: type[_sync_impl.ConnectionPool],
    cfg: Any,
    *,
    family: str = Family.STANDARD,
    hosts: Sequence[HostEntry] | None = None,
    rate_limiter: SessionRateLimiter | None = None,
) -> _sync_impl.ConnectionPool:
    if hosts is not None and len(hosts) == 0:
        raise ConfigError(
            "ConnectionPool.from_config explicit hosts 不能为空；使用 None 表示采用配置主站",
            context={
                "source": "ConnectionPool.from_config",
                "selector": "hosts",
            },
        )
    return _BASE_FROM_CONFIG(
        cls,
        cfg,
        family=family,
        hosts=hosts,
        rate_limiter=rate_limiter,
    )


setattr(_sync_impl.ConnectionPool, "from_config", classmethod(_from_config))
