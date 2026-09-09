# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Keep bestip probes outside the live ConnectionPool generation until commit.

``list(pool.hosts)`` copies only the container; the HostEntry objects remain the
same mutable instances owned by the current pool generation. Speed-test helpers
refresh probe RTT in place, so probing those objects bypasses generation-safe
``update_hosts``. Async executor cancellation makes that leak worse because the
worker can continue mutating the live pool after the coroutine is gone.

Both clients therefore probe detached HostEntry snapshots and commit observations
only through the canonical pool ``update_hosts`` boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from . import async_ as _async_impl
from . import sync as _sync_impl


def _probe_snapshot(pool: Any) -> list[Any]:
    return [replace(host) for host in pool.hosts]


def _sync_bestip(
    self: _sync_impl.TdxClient,
    *,
    timeout: float = 1.0,
    samples: int = 1,
    max_workers: int = 16,
    save_ranking: bool = True,
    keep_failures: bool = True,
) -> list[Any]:
    from ..transport.speedtest import rank_hosts, speedtest, speedtest_and_save

    hosts = _probe_snapshot(self._pool)
    if not hosts:
        return []
    if save_ranking:
        results = speedtest_and_save(
            hosts,
            family=self.family,
            timeout=timeout,
            samples=samples,
            max_workers=max_workers,
            keep_failures=keep_failures,
        )
    else:
        results = speedtest(
            hosts,
            family=self.family,
            timeout=timeout,
            samples=samples,
            max_workers=max_workers,
        )
    selected = results if keep_failures else [result for result in results if result.ok]
    self._pool.update_hosts(rank_hosts(selected))
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
    from ..transport.speedtest import rank_hosts, speedtest, speedtest_and_save

    hosts = _probe_snapshot(self._pool)
    if not hosts:
        return []
    loop = asyncio.get_running_loop()
    if save_ranking:
        results = await loop.run_in_executor(
            None,
            lambda: speedtest_and_save(
                hosts,
                family=self.family,
                timeout=timeout,
                samples=samples,
                max_workers=max_workers,
                keep_failures=keep_failures,
            ),
        )
    else:
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
    selected = results if keep_failures else [result for result in results if result.ok]
    await self._pool.update_hosts(rank_hosts(selected))
    return results


setattr(_sync_impl.TdxClient, "bestip", _sync_bestip)
setattr(_async_impl.AsyncTdxClient, "bestip", _async_bestip)
