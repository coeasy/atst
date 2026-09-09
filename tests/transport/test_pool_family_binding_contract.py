from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import ConnectionPool


def _host(family: str) -> HostEntry:
    return HostEntry(host="127.0.0.1", port=7709, family=family)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_unknown_family(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="family 非法"):
        pool_cls([_host(Family.STANDARD)], family="unknown", heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_empty_hosts_as_config_error(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="hosts 不能为空"):
        pool_cls([], family=Family.STANDARD, heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_host_from_another_family(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="host family 不匹配"):
        pool_cls([_host(Family.F10)], family=Family.STANDARD, heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_non_hostentry_sequence(pool_cls: type[Any]) -> None:
    with pytest.raises(ConfigError, match="只接受 HostEntry"):
        pool_cls([object()], family=Family.STANDARD, heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
def test_pool_rejects_duplicate_canonical_endpoints(pool_cls: type[Any]) -> None:
    first = HostEntry(host="127.0.0.1", port=7709, family=Family.STANDARD)
    equivalent = HostEntry(host=" 127.0.0.1 ", port=7709, family=Family.STANDARD)

    with pytest.raises(ConfigError, match="重复 canonical endpoint"):
        pool_cls([first, equivalent], family=Family.STANDARD, heartbeat_interval=0)


@pytest.mark.parametrize("pool_cls", [ConnectionPool, AsyncConnectionPool])
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"slots_per_host": 0}, "slots_per_host"),
        ({"slots_per_host": True}, "slots_per_host"),
        ({"timeout": 0.0}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
        ({"timeout": "3"}, "timeout"),
        ({"connect_timeout": 0.0}, "connect_timeout"),
        ({"connect_timeout": float("inf")}, "connect_timeout"),
        ({"heartbeat_interval": -1}, "heartbeat_interval"),
        ({"heartbeat_interval": 1.5}, "heartbeat_interval"),
        ({"heartbeat_cmd": -1}, "heartbeat_cmd"),
        ({"heartbeat_cmd": 65536}, "heartbeat_cmd"),
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
        ({"use_tls": 1}, "use_tls"),
        ({"handshake": "yes"}, "handshake"),
        ({"handshake_strict": 1}, "handshake_strict"),
        ({"handshake": False, "handshake_strict": True}, "handshake_strict"),
        ({"rate_limiter": object()}, "rate_limiter"),
    ],
)
def test_sync_async_pool_options_fail_closed_without_silent_coercion(
    pool_cls: type[Any],
    kwargs: dict[str, Any],
    message: str,
) -> None:
    options: dict[str, Any] = {"heartbeat_interval": 0}
    options.update(kwargs)
    with pytest.raises(ConfigError, match=message):
        pool_cls([_host(Family.STANDARD)], **options)


def test_async_rate_limiter_requires_nonblocking_contract() -> None:
    class BlockingOnlyLimiter:
        strict = False

        def acquire(self) -> None:
            return None

    with pytest.raises(ConfigError, match="try_acquire"):
        AsyncConnectionPool(
            [_host(Family.STANDARD)],
            heartbeat_interval=0,
            rate_limiter=BlockingOnlyLimiter(),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"keepalive": 1}, "keepalive"),
        ({"speedtest_threshold": 0}, "speedtest_threshold"),
        ({"speedtest_threshold": True}, "speedtest_threshold"),
        ({"idle_timeout": float("nan")}, "idle_timeout"),
        ({"on_host_down": object()}, "on_host_down"),
    ],
)
def test_sync_only_pool_options_fail_closed(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    options: dict[str, Any] = {"heartbeat_interval": 0}
    options.update(kwargs)
    with pytest.raises(ConfigError, match=message):
        ConnectionPool([_host(Family.STANDARD)], **options)


def test_documented_nonpositive_idle_timeout_still_disables_idle_sweep() -> None:
    pool = ConnectionPool(
        [_host(Family.STANDARD)],
        slots_per_host=1,
        heartbeat_interval=0,
        idle_timeout=-1.0,
    )
    try:
        assert pool.idle_timeout == -1.0
    finally:
        pool.close()


def test_sync_async_constructor_signatures_survive_hardening_wrapper() -> None:
    sync = inspect.signature(ConnectionPool.__init__)
    async_ = inspect.signature(AsyncConnectionPool.__init__)

    assert "hosts" in sync.parameters
    assert "family" in sync.parameters
    assert "hosts" in async_.parameters
    assert "family" in async_.parameters
    assert ConnectionPool.__init__.__module__ == "tstdx.transport._pool_family_hardening"
    assert AsyncConnectionPool.__init__.__module__ == "tstdx.transport._pool_family_hardening"


def test_valid_same_family_pool_still_constructs() -> None:
    sync = ConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    async_ = AsyncConnectionPool(
        [_host(Family.F10)],
        family=Family.F10,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        assert sync.family == Family.F10
        assert async_.family == Family.F10
    finally:
        sync.close()
        asyncio.run(async_.close())


def test_sync_pool_owns_fresh_canonical_host_snapshot() -> None:
    source = HostEntry(
        host=" 127.0.0.1 ",
        port=7709,
        family=Family.STANDARD,
        failures=2,
        live_rtt_ms=5.0,
    )
    pool = ConnectionPool(
        [source],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        owned = pool.hosts[0]
        assert owned is not source
        assert owned.host == "127.0.0.1"
        assert owned.failures == 2
        assert owned.live_rtt_ms == 5.0

        source.failures = 99
        source.live_rtt_ms = 999.0
        assert owned.failures == 2
        assert owned.live_rtt_ms == 5.0
        assert pool._slots[0].host is owned
    finally:
        pool.close()


def test_async_pool_owns_fresh_canonical_host_snapshot() -> None:
    source = HostEntry(
        host=" 127.0.0.1 ",
        port=7709,
        family=Family.STANDARD,
        failures=2,
        live_rtt_ms=5.0,
    )
    pool = AsyncConnectionPool(
        [source],
        family=Family.STANDARD,
        slots_per_host=1,
        heartbeat_interval=0,
    )
    try:
        owned = pool.hosts[0]
        assert owned is not source
        assert owned.host == "127.0.0.1"
        assert owned.failures == 2
        assert owned.live_rtt_ms == 5.0

        source.failures = 99
        source.live_rtt_ms = 999.0
        assert owned.failures == 2
        assert owned.live_rtt_ms == 5.0
        assert pool._slots[0].host is owned
    finally:
        asyncio.run(pool.close())
