# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Keep bestip probes outside the live ConnectionPool generation until commit.

``list(pool.hosts)`` copies only the container; the HostEntry objects remain the
same mutable instances owned by the current pool generation. Speed-test helpers
refresh probe RTT in place, so probing those objects bypasses generation-safe
``update_hosts``. Async executor cancellation makes that leak worse because the
worker can continue after the coroutine is gone.

Both clients therefore use the same two-phase contract: probe detached HostEntry
snapshots without persistent side effects, publish through canonical
``update_hosts`` first, and only then persist STANDARD ranking when requested.
The async executor performs observation only, so cancellation while probing
cannot mutate the pool or write ranking behind the caller's back. The sync path
also cannot leave a successful ranking write behind when pool publication fails.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from ..protocol.commands import Family
from . import async_ as _async_impl
from . import sync as _sync_impl


def _probe_snapshot(pool: Any) -> list[Any]:
    return [replace(host) for host in pool.hosts]


def _rank_entries(results: list[Any], *, keep_failures: bool) -> list[Any]:
    from ..transport.speedtest import rank_hosts

    selected = results if keep_failures else [result for result in results if result.ok]
    return rank_hosts(selected)


def _persist_standard(entries: list[Any], *, family: str, save_ranking: bool) -> None:
    if save_ranking and family == Family.STANDARD:
        from ..transport.hosts import RankingStore

        RankingStore().update(entries)


def _sync_bestip(
    self: _sync_impl.TdxClient,
    *,
    timeout: float = 1.0,
    samples: int = 1,
    max_workers: int = 16,
    save_ranking: bool = True,
    keep_failures: bool = True,
) -> list[Any]:
    from ..transport.speedtest import speedtest

    hosts = _probe_snapshot(self._pool)
    if not hosts:
        return []
    results = speedtest(
        hosts,
        family=self.family,
        timeout=timeout,
        samples=samples,
        max_workers=max_workers,
    )
    entries = _rank_entries(results, keep_failures=keep_failures)
    self._pool.update_hosts(entries)
    _persist_standard(entries, family=self.family, save_ranking=save_ranking)
    return results


async def _async_bestip(
    self: _async_impl.AsyncTdxClient,
    *,
    timeout: float = 1.0,
    samples: int = 1,
    max_workers: int = 16,
    save_ranking: bool = True,
    keep_failures: bool = True,
) -> list[Any]:
    from ..transport.speedtest import speedtest

    hosts = _probe_snapshot(self._pool)
    if not hosts:
        return []
    loop = asyncio.get_running_loop()
    # The executor performs network observation only. It receives detached hosts
    # and has no persistent-store access, so a cancelled coroutine leaves no
    # late-running thread capable of committing state.
    results = await loop.run_in_executor(
        None,
        lambda: speedtest(
            hosts,
            family=self.family,
            timeout=timeout,
            samples=samples,
            max_workers=max_workers,
        ),
    )
    entries = _rank_entries(results, keep_failures=keep_failures)

    # Generation-safe pool publication is the first commit boundary. Persistent
    # STANDARD ranking is written only after that await completes successfully.
    await self._pool.update_hosts(entries)
    _persist_standard(entries, family=self.family, save_ranking=save_ranking)
    return results


_sync_impl.TdxClient.bestip = _sync_bestip  # type: ignore[method-assign]
_async_impl.AsyncTdxClient.bestip = _async_bestip  # type: ignore[method-assign]
