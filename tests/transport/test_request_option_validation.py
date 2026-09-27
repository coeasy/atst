from __future__ import annotations

import asyncio

import pytest

from atst.errors import ConfigError
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool


def _host() -> HostEntry:
    return HostEntry("127.0.0.1", 7709)


class _ExplodingLimiter:
    strict = False

    def acquire(self) -> None:
        raise AssertionError("invalid request option must fail before rate limiting")

    def try_acquire(self) -> bool:
        raise AssertionError("invalid request option must fail before rate limiting")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"retry": 1}, "retry"),
        ({"retry": "yes"}, "retry"),
        ({"compress": 1}, "compress"),
        ({"compress": "no"}, "compress"),
        ({"timeout": True}, "timeout"),
        ({"timeout": 0.0}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
    ],
)
def test_sync_request_rejects_coerced_options_before_runtime_side_effects(
    kwargs: dict[str, object],
    message: str,
) -> None:
    pool = ConnectionPool(
        [_host()],
        slots_per_host=1,
        heartbeat_interval=0,
        handshake=False,
        rate_limiter=_ExplodingLimiter(),
    )
    try:
        with pytest.raises(ConfigError, match=message):
            pool.request(0x0530, **kwargs)  # type: ignore[arg-type]
        assert pool._slots[0].conn is None
    finally:
        pool.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"expect_count": 1}, "expect_count"),
        ({"expect_count": "yes"}, "expect_count"),
        ({"timeout": False}, "timeout"),
        ({"timeout": -1.0}, "timeout"),
        ({"timeout": float("inf")}, "timeout"),
    ],
)
def test_sync_request_multi_rejects_coerced_options_before_runtime_side_effects(
    kwargs: dict[str, object],
    message: str,
) -> None:
    pool = ConnectionPool(
        [_host()],
        slots_per_host=1,
        heartbeat_interval=0,
        handshake=False,
        rate_limiter=_ExplodingLimiter(),
    )
    try:
        with pytest.raises(ConfigError, match=message):
            pool.request_multi(0x0530, **kwargs)  # type: ignore[arg-type]
        assert pool._slots[0].conn is None
    finally:
        pool.close()


def test_async_request_and_request_multi_reject_invalid_options_before_side_effects() -> None:
    async def run() -> None:
        pool = AsyncConnectionPool(
            [_host()],
            slots_per_host=1,
            heartbeat_interval=0,
            handshake=False,
            rate_limiter=_ExplodingLimiter(),
        )
        try:
            with pytest.raises(ConfigError, match="timeout"):
                await pool.request(0x0530, timeout=True)
            with pytest.raises(ConfigError, match="expect_count"):
                await pool.request_multi(0x0530, expect_count=1)  # type: ignore[arg-type]
            with pytest.raises(ConfigError, match="timeout"):
                await pool.request_multi(0x0530, timeout=float("nan"))
            assert pool._slots[0].conn is None
        finally:
            await pool.close()

    asyncio.run(run())
