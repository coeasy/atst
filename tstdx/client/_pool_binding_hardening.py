# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail closed when a real injected transport pool disagrees with client family.

Tests and advanced integrations may inject opaque pool doubles that predate the
``family`` attribute. Those remain a compatibility seam. Real tstdx connection
pools always declare ``family``; when present it is authoritative and must match
the client/subclient protocol family exactly. The requested client family itself
is always canonical, even when the injected pool is opaque.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import sync as _sync_impl

_SYNC_INIT = _sync_impl.TdxClient.__init__
_ASYNC_INIT = _async_impl.AsyncTdxClient.__init__
_MISSING = object()
_VALID_FAMILIES = frozenset(
    {Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10}
)


def _require_pool_family(pool: Any | None, requested_family: str) -> None:
    if requested_family not in _VALID_FAMILIES:
        raise ConfigError(
            f"client family 非法: {requested_family!r}",
            context={
                "client_family": requested_family,
                "provider_switch_allowed": False,
            },
        )
    if pool is None:
        return
    pool_family = getattr(pool, "family", _MISSING)
    if pool_family is _MISSING:
        # Backward-compatible test/adapter seam. Repository-owned pools always
        # expose family, so production provenance is still fail-closed.
        return
    if pool_family != requested_family:
        raise ConfigError(
            "client/pool family 不匹配: "
            f"client={requested_family!r}, pool={pool_family!r}",
            context={
                "client_family": requested_family,
                "pool_family": pool_family,
                "provider_switch_allowed": False,
            },
        )


def _sync_init(
    self: _sync_impl.TdxClient,
    hosts: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    timeout: float = 5.0,
    max_retries: int = 3,
    pool: Any | None = None,
    **pool_kwargs: Any,
) -> None:
    _require_pool_family(pool, family)
    _SYNC_INIT(
        self,
        hosts,
        family=family,
        timeout=timeout,
        max_retries=max_retries,
        pool=pool,
        **pool_kwargs,
    )


def _async_init(
    self: _async_impl.AsyncTdxClient,
    hosts: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    timeout: float = 5.0,
    max_retries: int = 3,
    pool: Any | None = None,
    **pool_kwargs: Any,
) -> None:
    _require_pool_family(pool, family)
    _ASYNC_INIT(
        self,
        hosts,
        family=family,
        timeout=timeout,
        max_retries=max_retries,
        pool=pool,
        **pool_kwargs,
    )


setattr(_sync_impl.TdxClient, "__init__", _sync_init)
setattr(_async_impl.AsyncTdxClient, "__init__", _async_init)
