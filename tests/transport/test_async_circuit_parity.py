from __future__ import annotations

import asyncio
import time

import pytest

from tstdx.errors import ConnectionFailed
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import CIRCUIT_COOLDOWN_SECONDS


def _pool(host: HostEntry) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        [host],
        slots_per_host=1,
        heartbeat_interval=0,
        handshake=False,
    )


def test_direct_async_module_import_has_canonical_circuit_hardening() -> None:
    pool = _pool(HostEntry("127.0.0.1", 7709))

    assert callable(getattr(pool, "_circuit_allows", None))
    assert callable(getattr(pool, "_release_probe_token", None))
    assert callable(getattr(pool, "_mark_failure", None))
    assert callable(getattr(pool, "_mark_success", None))


def test_async_half_open_allows_exactly_one_probe_under_concurrency() -> None:
    async def main() -> tuple[list[bool], HostEntry]:
        host = HostEntry("127.0.0.1", 7709)
        host.circuit = "open"
        host.circuit_opened_at = time.time() - CIRCUIT_COOLDOWN_SECONDS - 1
        pool = _pool(host)
        try:
            allowed = await asyncio.gather(*(pool._circuit_allows(host) for _ in range(16)))
            return allowed, host
        finally:
            await pool.close()

    allowed, host = asyncio.run(main())

    assert sum(allowed) == 1
    assert host.circuit == "half_open"
    assert host.circuit_probe_inflight is True


def test_async_half_open_failure_immediately_reopens_and_releases_token() -> None:
    async def main() -> HostEntry:
        pool = _pool(HostEntry("127.0.0.1", 7709))
        slot = pool._slots[0]
        # A directly constructed pool owns a fresh runtime-health generation, so
        # the circuit state under test lives on the pool's own host.
        slot.host.circuit = "open"
        slot.host.circuit_opened_at = time.time() - CIRCUIT_COOLDOWN_SECONDS - 1
        try:
            assert await pool._circuit_allows(slot.host) is True
            await pool._mark_failure(
                slot,
                ConnectionFailed("probe failed"),
                generation=slot.generation,
            )
            return slot.host
        finally:
            await pool.close()

    host = asyncio.run(main())

    assert host.circuit == "open"
    assert host.circuit_probe_inflight is False
    assert host.circuit_opened_at > 0
    assert host.failures == 1


def test_async_success_resets_circuit_and_records_live_health(
    seed_pool_health,
) -> None:
    async def main() -> HostEntry:
        pool = _pool(HostEntry("127.0.0.1", 7709))
        slot = pool._slots[0]
        seed_pool_health(
            pool,
            failures=4,
            biz_failures=2,
            circuit="half_open",
            consec_weighted=4.0,
            circuit_probe_inflight=True,
        )
        try:
            await pool._mark_success(slot, generation=slot.generation, rtt_ms=7.5)
            return slot.host
        finally:
            await pool.close()

    host = asyncio.run(main())

    assert host.failures == 0
    assert host.biz_failures == 0
    assert host.circuit == "healthy"
    assert host.circuit_probe_inflight is False
    assert host.consec_weighted == 0.0
    assert host.circuit_opened_at == 0.0
    assert host.live_rtt_ms == 7.5
    assert host.live_ok_at is not None


def test_old_async_generation_health_result_cannot_mutate_new_host(
    seed_pool_health,
) -> None:
    async def main() -> tuple[HostEntry, HostEntry, HostEntry]:
        old = HostEntry("127.0.0.1", 7709, live_rtt_ms=20.0)
        pool = _pool(old)
        old_slot = pool._slots[0]
        seed_pool_health(pool, live_rtt_ms=20.0)
        old_generation = old_slot.generation
        fresh = HostEntry("127.0.0.1", 7709, rtt_ms=2.0)
        published = (await pool.update_hosts([fresh]))[0]
        try:
            # A late result from the retired generation must not rewrite the
            # newly published live host.
            await pool._mark_success(old_slot, generation=old_generation, rtt_ms=1.0)
            return published, old_slot.host, old
        finally:
            await pool.close()

    published, live_host, old = asyncio.run(main())

    assert live_host is published
    assert published.rtt_ms == 2.0
    assert published.live_rtt_ms == 20.0
    assert old.live_rtt_ms == 20.0


def test_cancelled_half_open_request_releases_token_without_marking_failure() -> None:
    async def main() -> HostEntry:
        pool = _pool(HostEntry("127.0.0.1", 7709))
        slot = pool._slots[0]
        slot.host.circuit = "open"
        slot.host.circuit_opened_at = time.time() - CIRCUIT_COOLDOWN_SECONDS - 1

        async def cancelled_acquire(_slot):
            raise asyncio.CancelledError

        pool._acquire_lease = cancelled_acquire
        try:
            with pytest.raises(asyncio.CancelledError):
                await pool.request(0x0530, b"x")
            return slot.host
        finally:
            await pool.close()

    host = asyncio.run(main())

    assert host.circuit == "half_open"
    assert host.circuit_probe_inflight is False
    assert host.failures == 0
    assert host.biz_failures == 0
    assert host.last_error == ""
