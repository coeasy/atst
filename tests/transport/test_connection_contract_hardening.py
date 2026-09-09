from __future__ import annotations

import asyncio
import ssl
from typing import Any

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.async_ import AsyncTcpConnection
from tstdx.transport.base import TcpConnection


@pytest.mark.parametrize("connection_cls", [TcpConnection, AsyncTcpConnection])
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0.0}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
        ({"connect_timeout": 0.0}, "connect_timeout"),
        ({"connect_timeout": float("inf")}, "connect_timeout"),
        ({"use_tls": 1}, "use_tls"),
        ({"slot_id": -1}, "slot_id"),
        ({"slot_id": True}, "slot_id"),
        ({"handshake": 1}, "handshake"),
        ({"handshake_strict": 1}, "handshake_strict"),
        ({"handshake": False, "handshake_strict": True}, "handshake_strict"),
        ({"handshake": False, "handshake_blob": b"setup"}, "handshake_blob"),
        ({"handshake_blob": "setup"}, "handshake_blob"),
        ({"family": "unknown"}, "family"),
    ],
)
def test_direct_connection_constructor_rejects_coerced_or_ignored_options(
    connection_cls: type[Any],
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        connection_cls("127.0.0.1", **kwargs)


def test_sync_connection_rejects_non_boolean_keepalive() -> None:
    with pytest.raises(ConfigError, match="keepalive"):
        TcpConnection("127.0.0.1", keepalive=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("connection_cls", [TcpConnection, AsyncTcpConnection])
def test_tls_context_cannot_be_silently_ignored_when_tls_is_disabled(
    connection_cls: type[Any],
) -> None:
    context = ssl.create_default_context()

    with pytest.raises(ConfigError, match="use_tls=False"):
        connection_cls("127.0.0.1", use_tls=False, tls_context=context)


def test_direct_connections_canonicalize_endpoint_identity_without_connecting() -> None:
    sync = TcpConnection(
        " 127.0.0.1 ",
        "7709",  # type: ignore[arg-type]
        family=Family.STANDARD,
        handshake=False,
    )
    async_ = AsyncTcpConnection(
        " 127.0.0.1 ",
        "7709",  # type: ignore[arg-type]
        family=Family.STANDARD,
        handshake=False,
    )

    assert sync.addr == ("127.0.0.1", 7709)
    assert async_.addr == ("127.0.0.1", 7709)
    assert sync.connected is False
    assert async_.connected is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"check_seq": 1}, "check_seq"),
        ({"compress": "no"}, "compress"),
        ({"timeout": True}, "timeout"),
        ({"timeout": -1.0}, "timeout"),
    ],
)
def test_sync_direct_request_rejects_invalid_options_before_connect(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    conn = TcpConnection("127.0.0.1", handshake=False)

    with pytest.raises(ConfigError, match=message):
        conn.request(0x0530, **kwargs)
    assert conn.connected is False


def test_async_direct_request_rejects_invalid_options_before_connect() -> None:
    async def run() -> None:
        conn = AsyncTcpConnection("127.0.0.1", handshake=False)
        with pytest.raises(ConfigError, match="check_seq"):
            await conn.request(0x0530, check_seq=1)  # type: ignore[arg-type]
        with pytest.raises(ConfigError, match="compress"):
            await conn.request(0x0530, compress=1)  # type: ignore[arg-type]
        with pytest.raises(ConfigError, match="timeout"):
            await conn.request(0x0530, timeout=float("nan"))
        assert conn.connected is False

    asyncio.run(run())


def test_direct_connection_public_wiring_uses_contract_hardening() -> None:
    assert TcpConnection.__init__.__module__ == "tstdx.transport._connection_contract_hardening"
    assert AsyncTcpConnection.__init__.__module__ == "tstdx.transport._connection_contract_hardening"
    assert TcpConnection.request.__module__ == "tstdx.transport._connection_contract_hardening"
    assert AsyncTcpConnection.request.__module__ == "tstdx.transport._connection_contract_hardening"
