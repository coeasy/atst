# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Isolate resolved host state from selector prototypes and caller-owned objects.

``POOL_BY_FAMILY`` and ``DEFAULT_HOST_POOL`` are repository-owned prototypes.
Connection pools mutate HostEntry live-health and circuit fields, so returning a
shallow list of those prototypes leaks one client's runtime health into later
clients in the same process.  Explicit selector objects have the same ownership
problem if handed directly to the pool.

The canonical resolver therefore returns a fresh HostEntry snapshot for every
entry, after all selector/ranking policy has been applied.  Endpoint identity,
static verification and observations are preserved, while mutable object
ownership is request/client-local.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from . import hosts as _impl

_BASE_RESOLVE_HOSTS = _impl.resolve_hosts


def _resolve_hosts_isolated(
    servers: Sequence[Any] | None = None,
    *,
    family: str = _impl.Family.STANDARD,
    ranking: _impl.RankingStore | None = None,
    ranking_file: str | None = None,
    use_ranking: bool = True,
    max_hosts: int = 8,
) -> list[_impl.HostEntry]:
    resolved = _BASE_RESOLVE_HOSTS(
        servers,
        family=family,
        ranking=ranking,
        ranking_file=ranking_file,
        use_ranking=use_ranking,
        max_hosts=max_hosts,
    )
    return [replace(entry) for entry in resolved]


setattr(_impl, "resolve_hosts", _resolve_hosts_isolated)
