# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Isolate and de-duplicate resolved host state at the selector boundary.

``POOL_BY_FAMILY`` and ``DEFAULT_HOST_POOL`` are repository-owned prototypes.
Connection pools mutate HostEntry live-health and circuit fields, so returning a
shallow list of those prototypes leaks one client's runtime health into later
clients in the same process. Explicit selector objects have the same ownership
problem if handed directly to the pool.

The canonical resolver therefore resolves the full supported candidate window,
rejects duplicate canonical endpoints before user ``max_hosts`` truncation, and
returns a fresh HostEntry snapshot for every retained entry. Endpoint identity,
static verification and observations are preserved, while mutable object
ownership is request/client-local.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from . import hosts as _impl

_BASE_RESOLVE_HOSTS = _impl.resolve_hosts


def _resolve_hosts_isolated(
    servers: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    ranking: _impl.RankingStore | None = None,
    ranking_file: str | None = None,
    use_ranking: bool = True,
    max_hosts: int = 8,
) -> list[_impl.HostEntry]:
    limit = _impl._require_max_hosts(max_hosts)
    resolved = _BASE_RESOLVE_HOSTS(
        servers,
        family=family,
        ranking=ranking,
        ranking_file=ranking_file,
        use_ranking=use_ranking,
        # Duplicate detection must happen before the caller-requested slice;
        # otherwise an invalid duplicate selector can be hidden by max_hosts=1.
        max_hosts=_impl._MAX_RESOLVED_HOSTS,
    )

    seen: set[str] = set()
    for index, entry in enumerate(resolved):
        if entry.key in seen:
            raise ConfigError(
                f"resolve_hosts 存在重复 canonical endpoint: {entry.key}",
                context={
                    "family": family,
                    "host": entry.key,
                    "index": index,
                },
            )
        seen.add(entry.key)

    return [replace(entry) for entry in resolved[:limit]]


_impl.resolve_hosts = _resolve_hosts_isolated
