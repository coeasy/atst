# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Harden public pool factory selector/config semantics.

``ConnectionPool.from_config`` historically had two silent-fallback paths:

* truthiness decided whether an explicit ``hosts`` override existed, so
  ``hosts=[]`` looked identical to ``hosts=None`` and fell back to configured or
  default network endpoints;
* any non-``Config`` object was silently treated as ``None``, so a misshaped
  explicit configuration could be ignored while the pool continued with
  defaults.

Both inputs are authoritative in v12.  Only ``hosts=None`` means "use config"
and only ``cfg=None`` means "no config".  Contradictory or malformed explicit
inputs fail closed before any pool is constructed.
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

_BASE_FROM_CONFIG = _sync_impl.ConnectionPool.from_config.__func__  # type: ignore[attr-defined]


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
    from ..config.schema import Config

    if cfg is not None and not isinstance(cfg, Config):
        raise ConfigError(
            "ConnectionPool.from_config cfg 必须是 Config 或 None；"
            f"收到 {type(cfg).__name__}",
            context={
                "source": "ConnectionPool.from_config",
                "parameter": "cfg",
                "value_type": type(cfg).__name__,
            },
        )
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


_sync_impl.ConnectionPool.from_config = classmethod(_from_config)  # type: ignore[assignment,method-assign]
