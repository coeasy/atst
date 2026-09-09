# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail closed when injected transport-pool configuration is ambiguous.

Tests and advanced integrations may inject opaque pool doubles that predate the
``family`` attribute. Those remain a compatibility seam. Real tstdx connection
pools always declare ``family``; when present it is authoritative and must match
the client/subclient protocol family exactly.

Supplying an already-constructed pool also means pool-construction inputs cannot
silently pretend to apply. A non-empty ``hosts`` selector, non-default
``max_retries`` or extra ``**pool_kwargs`` would otherwise be ignored by the
canonical client constructors. Those combinations now fail closed instead of
creating a client whose visible configuration disagrees with its transport.
``timeout`` remains valid because the client uses it for per-request deadlines.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import sync as _sync_impl

_SYNC_INIT = _sync_impl.TdxClient.__init__
_ASYNC_INIT = _async_impl.AsyncTdxClient.__init__
_MISSING = object()
_DEFAULT_MAX_RETRIES = 3
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


def _require_unambiguous_pool_binding(
    *,
    pool: Any | None,
    family: str,
    hosts: Sequence[Any] | None,
    max_retries: int,
    pool_kwargs: Mapping[str, Any],
) -> None:
    """Validate one injected-pool binding without accepting ignored settings."""

    _require_pool_family(pool, family)
    if pool is None:
        return

    if hosts is not None:
        raise ConfigError(
            "注入 pool= 时不能同时传 hosts=；hosts 不会重新构造已有连接池",
            context={
                "client_family": family,
                "ignored_parameter": "hosts",
                "provider_switch_allowed": False,
            },
        )
    if max_retries != _DEFAULT_MAX_RETRIES:
        raise ConfigError(
            "注入 pool= 时不能覆盖 max_retries；请在构造连接池时设置",
            context={
                "client_family": family,
                "ignored_parameter": "max_retries",
                "requested_max_retries": max_retries,
                "provider_switch_allowed": False,
            },
        )
    if pool_kwargs:
        names = sorted(pool_kwargs)
        raise ConfigError(
            "注入 pool= 时不能再传连接池构造参数: " + ", ".join(names),
            context={
                "client_family": family,
                "ignored_parameters": names,
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
    _require_unambiguous_pool_binding(
        pool=pool,
        family=family,
        hosts=hosts,
        max_retries=max_retries,
        pool_kwargs=pool_kwargs,
    )
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
    _require_unambiguous_pool_binding(
        pool=pool,
        family=family,
        hosts=hosts,
        max_retries=max_retries,
        pool_kwargs=pool_kwargs,
    )
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
