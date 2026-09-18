# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Fail-closed configuration contract for direct sync/async connections.

Pool construction already validates endpoint identity and transport options, but
``TcpConnection`` and ``AsyncTcpConnection`` are public APIs too. Their legacy
constructors used ``int()``/``float()`` and truthiness, allowing malformed values
or silently ignored TLS/handshake configuration to bypass the pool boundary.

This layer gives direct connections the same canonical endpoint, finite timeout,
strict boolean and non-ignored option semantics without changing socket/protocol
implementation internals or degrading the public type signatures.
"""

from __future__ import annotations

import math
import ssl
from typing import Any

from ..codec.framing import DEFAULT_7709_SPEC, FrameSpec, ResponseFrame
from ..errors import ConfigError
from ..protocol.commands import Family
from . import async_ as _async_impl
from . import base as _sync_impl
from .hosts import parse_server

_SYNC_INIT = _sync_impl.TcpConnection.__init__
_ASYNC_INIT = _async_impl.AsyncTcpConnection.__init__
_SYNC_REQUEST = _sync_impl.TcpConnection.request
_ASYNC_REQUEST = _async_impl.AsyncTcpConnection.request


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} 必须是 bool，收到 {value!r}")
    return value


def _require_timeout(name: str, value: Any, *, allow_none: bool = False) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(f"{name} 不能为 None")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是正有限数值，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ConfigError(f"{name} 必须是正有限数值，收到 {value!r}")
    return normalized


def _require_slot_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"slot_id 必须是非负整数，收到 {value!r}")
    return value


def _validate_optional_bytes(name: str, value: Any) -> bytes | None:
    if value is not None and not isinstance(value, bytes):
        raise ConfigError(f"{name} 必须是 bytes 或 None，收到 {type(value).__name__}")
    return value


def _validate_common(
    *,
    host: Any,
    port: Any,
    family: Any,
    timeout: Any,
    connect_timeout: Any,
    use_tls: Any,
    tls_context: Any,
    slot_id: Any,
    handshake: Any,
    handshake_strict: Any,
    handshake_blob: Any,
) -> tuple[str, int, str, float, float | None, bool, int, bool, bool, bytes | None]:
    entry = parse_server((host, port), family=family)
    request_timeout = _require_timeout("timeout", timeout)
    assert request_timeout is not None
    connect = _require_timeout("connect_timeout", connect_timeout, allow_none=True)
    tls_enabled = _require_bool("use_tls", use_tls)
    slot = _require_slot_id(slot_id)
    handshake_enabled = _require_bool("handshake", handshake)
    strict = _require_bool("handshake_strict", handshake_strict)
    blob = _validate_optional_bytes("handshake_blob", handshake_blob)

    if tls_context is not None and not isinstance(tls_context, ssl.SSLContext):
        raise ConfigError(
            "tls_context 必须是 ssl.SSLContext 或 None，"
            f"收到 {type(tls_context).__name__}"
        )
    if tls_context is not None and not tls_enabled:
        raise ConfigError("use_tls=False 时 tls_context 不会生效；请启用 TLS 或移除 tls_context")
    if not handshake_enabled and strict:
        raise ConfigError("handshake=False 时 handshake_strict=True 无效")
    if not handshake_enabled and blob is not None:
        raise ConfigError("handshake=False 时 handshake_blob 不会生效")

    return (
        entry.host,
        entry.port,
        entry.family,
        request_timeout,
        connect,
        tls_enabled,
        slot,
        handshake_enabled,
        strict,
        blob,
    )


def _sync_init(
    self,
    host: str,
    port: int = 7709,
    *,
    timeout: float = 3.0,
    connect_timeout: float | None = None,
    spec: FrameSpec = DEFAULT_7709_SPEC,
    use_tls: bool = False,
    tls_context: ssl.SSLContext | None = None,
    keepalive: bool = True,
    slot_id: int = 0,
    family: str = Family.STANDARD,
    handshake: bool = True,
    handshake_strict: bool = False,
    handshake_blob: bytes | None = None,
) -> None:
    (
        canonical_host,
        canonical_port,
        canonical_family,
        request_timeout,
        connect,
        tls_enabled,
        slot,
        handshake_enabled,
        strict,
        blob,
    ) = _validate_common(
        host=host,
        port=port,
        family=family,
        timeout=timeout,
        connect_timeout=connect_timeout,
        use_tls=use_tls,
        tls_context=tls_context,
        slot_id=slot_id,
        handshake=handshake,
        handshake_strict=handshake_strict,
        handshake_blob=handshake_blob,
    )
    keepalive_enabled = _require_bool("keepalive", keepalive)
    _SYNC_INIT(
        self,
        canonical_host,
        canonical_port,
        timeout=request_timeout,
        connect_timeout=connect,
        spec=spec,
        use_tls=tls_enabled,
        tls_context=tls_context,
        keepalive=keepalive_enabled,
        slot_id=slot,
        family=canonical_family,
        handshake=handshake_enabled,
        handshake_strict=strict,
        handshake_blob=blob,
    )


def _async_init(
    self,
    host: str,
    port: int = 7709,
    *,
    timeout: float = 3.0,
    connect_timeout: float | None = None,
    spec: FrameSpec = DEFAULT_7709_SPEC,
    use_tls: bool = False,
    tls_context: ssl.SSLContext | None = None,
    slot_id: int = 0,
    family: str = Family.STANDARD,
    handshake: bool = True,
    handshake_strict: bool = False,
    handshake_blob: bytes | None = None,
) -> None:
    (
        canonical_host,
        canonical_port,
        canonical_family,
        request_timeout,
        connect,
        tls_enabled,
        slot,
        handshake_enabled,
        strict,
        blob,
    ) = _validate_common(
        host=host,
        port=port,
        family=family,
        timeout=timeout,
        connect_timeout=connect_timeout,
        use_tls=use_tls,
        tls_context=tls_context,
        slot_id=slot_id,
        handshake=handshake,
        handshake_strict=handshake_strict,
        handshake_blob=handshake_blob,
    )
    _ASYNC_INIT(
        self,
        canonical_host,
        canonical_port,
        timeout=request_timeout,
        connect_timeout=connect,
        spec=spec,
        use_tls=tls_enabled,
        tls_context=tls_context,
        slot_id=slot,
        family=canonical_family,
        handshake=handshake_enabled,
        handshake_strict=strict,
        handshake_blob=blob,
    )


def _sync_request(
    self,
    method: int,
    body: bytes = b"",
    *,
    check_seq: bool = True,
    compress: bool = False,
    timeout: float | None = None,
) -> ResponseFrame:
    check = _require_bool("check_seq", check_seq)
    compressed = _require_bool("compress", compress)
    request_timeout = _require_timeout("timeout", timeout, allow_none=True)
    return _SYNC_REQUEST(
        self,
        method,
        body,
        check_seq=check,
        compress=compressed,
        timeout=request_timeout,
    )


async def _async_request(
    self,
    method: int,
    body: bytes = b"",
    *,
    check_seq: bool = True,
    compress: bool = False,
    timeout: float | None = None,
) -> ResponseFrame:
    check = _require_bool("check_seq", check_seq)
    compressed = _require_bool("compress", compress)
    request_timeout = _require_timeout("timeout", timeout, allow_none=True)
    return await _ASYNC_REQUEST(
        self,
        method,
        body,
        check_seq=check,
        compress=compressed,
        timeout=request_timeout,
    )


_sync_impl.TcpConnection.__init__ = _sync_init  # type: ignore[method-assign]
_async_impl.AsyncTcpConnection.__init__ = _async_init  # type: ignore[method-assign]
_sync_impl.TcpConnection.request = _sync_request  # type: ignore[method-assign]
_async_impl.AsyncTcpConnection.request = _async_request  # type: ignore[method-assign]
