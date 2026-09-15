# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""AsyncConnectionPool lifecycle hardening installed onto the legacy module.

``transport.async_`` is a long-lived public import path. Keep that module and
class identity while installing the missing async equivalents of the synchronous
circuit/request-health state machine. This module is imported once by
``tstdx.transport`` before callers can receive ``transport.async_.AsyncConnectionPool``.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections.abc import AsyncIterator
from typing import Any

from ..errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
    AllHostsUnreachable,
    ConfigError,
    ConnectionClosed,
    ConnectionFailed,
    FramingError,
    TdxError,
)
from ..observability import metrics
from . import async_ as _impl
from .pool import (
    BIZ_FAILURE_WEIGHT,
    CIRCUIT_COOLDOWN_SECONDS,
    CIRCUIT_DEGRADED_AT,
    CIRCUIT_OPEN_AT,
)

_LOG = logging.getLogger("tstdx.transport")


async def _circuit_allows(self: _impl.AsyncConnectionPool, host: Any) -> bool:
    """Async mirror of the synchronous single-probe circuit admission gate."""

    async with self._lock:
        if host.circuit == "open":
            if time.time() - host.circuit_opened_at < CIRCUIT_COOLDOWN_SECONDS:
                return False
            if host.circuit_probe_inflight:
                return False
            host.circuit = "half_open"
            host.circuit_probe_inflight = True
            _LOG.info("异步主站 %s 熔断冷却到期，转 HALF_OPEN 放行单次探测", host.key)
            return True
        if host.circuit == "half_open":
            if host.circuit_probe_inflight:
                return False
            host.circuit_probe_inflight = True
            return True
        return True


async def _select_allowed_slot(
    self: _impl.AsyncConnectionPool,
    *,
    exclude_hosts: set[str] | None = None,
) -> _impl.AsyncSlot | None:
    """Pick the first ordered slot admitted by the circuit state machine."""

    slots = self._ordered()
    if exclude_hosts:
        preferred = [slot for slot in slots if slot.host.key not in exclude_hosts]
        candidates = preferred or slots
    else:
        candidates = slots
    for candidate in candidates:
        if await _circuit_allows(self, candidate.host):
            return candidate
    return None


async def _release_probe_token(
    self: _impl.AsyncConnectionPool,
    slot: _impl.AsyncSlot,
    generation: int | None,
) -> None:
    """Release a claimed HALF_OPEN token without fabricating host health."""

    if generation is not None and not await self._slot_is_current(slot, generation):
        return
    async with self._lock:
        if slot.host.circuit == "half_open":
            slot.host.circuit_probe_inflight = False


async def _mark_failure(
    self: _impl.AsyncConnectionPool,
    slot: _impl.AsyncSlot,
    exc: BaseException,
    *,
    generation: int | None = None,
    conn: _impl.AsyncTcpConnection | None = None,
) -> None:
    """Advance async live-health only when the producing generation is current."""

    current = generation is None or await self._slot_is_current(slot, generation)
    if current:
        async with self._lock:
            host = slot.host
            was_half_open = host.circuit == "half_open" or host.circuit_probe_inflight
            host.failures += 1
            if not isinstance(exc, ConnectionFailed):
                host.biz_failures += 1
            host.consec_weighted += (
                1.0 if isinstance(exc, ConnectionFailed) else BIZ_FAILURE_WEIGHT
            )
            host.circuit_probe_inflight = False
            if was_half_open or host.consec_weighted >= CIRCUIT_OPEN_AT:
                if host.circuit != "open" or host.circuit_opened_at == 0.0:
                    host.circuit_opened_at = time.time()
                host.circuit = "open"
            elif host.consec_weighted >= CIRCUIT_DEGRADED_AT:
                host.circuit = "degraded"
            host.last_error = f"{type(exc).__name__}: {exc}"
    metrics.record_error(type(exc).__name__)
    await self._drop(slot, expected=conn)


async def _mark_success(
    self: _impl.AsyncConnectionPool,
    slot: _impl.AsyncSlot,
    *,
    generation: int | None = None,
    rtt_ms: float | None = None,
) -> None:
    """Reset async live-health/circuit only for the current generation."""

    if generation is not None and not await self._slot_is_current(slot, generation):
        return
    async with self._lock:
        host = slot.host
        host.failures = 0
        host.biz_failures = 0
        host.last_error = ""
        host.last_ok = time.time()
        if rtt_ms is not None:
            host.live_rtt_ms = max(0.0, float(rtt_ms))
            host.live_ok_at = host.last_ok
        host.circuit = "healthy"
        host.circuit_probe_inflight = False
        host.consec_weighted = 0.0
        host.circuit_opened_at = 0.0


def _circuit_unreachable(method: int, *, attempts: int = 0) -> AllHostsUnreachable:
    cause = ConnectionFailed("所有候选主站均处于熔断门禁")
    return AllHostsUnreachable(
        f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
        context={"hosts": [], "method": hex(method), "attempts": attempts},
        cause=cause,
    )


def _require_positive_frame_limit(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{field} 必须是正整数，收到 {value!r}")
    return value


def _require_bool_option(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 bool，收到 {value!r}")
    return value


def _require_request_timeout(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    return timeout


async def _request(
    self: _impl.AsyncConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    timeout: float | None = None,
) -> _impl.ResponseFrame:
    """Async request path with sync-equivalent host coverage and circuit semantics."""

    request_timeout = _require_request_timeout(timeout)
    self._ensure_open()
    max_attempts = self.max_retries + 1
    distinct_hosts = len({slot.host.key for slot in self._slots})
    max_attempts = max(max_attempts, distinct_hosts)
    last_exc: BaseException | None = None
    tried: list[str] = []
    started = time.perf_counter()

    for attempt in range(max_attempts):
        self._ensure_open()
        await self._acquire_rate()
        slot = await _select_allowed_slot(self, exclude_hosts=set(tried))
        if slot is None:
            last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
            break
        tried.append(slot.host.key)

        conn: _impl.AsyncTcpConnection | None = None
        generation: int | None = None
        attempt_started = time.perf_counter()
        try:
            conn, generation = await self._acquire_lease(slot)
            try:
                frame = await conn.request(method, body, timeout=request_timeout)
            finally:
                await self._release_lease(slot, conn)
        except TdxError as exc:
            last_exc = exc
            await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            advice = exc.advice
            if attempt + 1 >= max_attempts or not advice.retryable:
                break
            if advice.switch_host:
                _LOG.info("异步换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                self._rotate_away(slot.host.key)
            if advice.backoff:
                delay = advice.backoff * (2**attempt) * (0.75 + 0.5 * random.random())
                await asyncio.sleep(delay)
            continue
        except asyncio.CancelledError:
            await _release_probe_token(self, slot, generation)
            raise
        except Exception as exc:
            last_exc = exc
            await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            if attempt + 1 >= max_attempts:
                break
            await asyncio.sleep(0.05 * (attempt + 1))
            continue
        except BaseException:
            await _release_probe_token(self, slot, generation)
            raise

        await _mark_success(
            self,
            slot,
            generation=generation,
            rtt_ms=(time.perf_counter() - attempt_started) * 1000.0,
        )
        metrics.record_request(
            command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
        )
        return frame

    metrics.record_request(command=f"0x{method:04x}", ok=False)
    raise AllHostsUnreachable(
        f"所有主站均不可达（已尝试 {tried}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
        context={
            "hosts": tried,
            "method": hex(method),
            "attempts": max_attempts,
            "last_error": str(last_exc) if last_exc else None,
        },
        cause=last_exc,
    )


async def _request_multi(
    self: _impl.AsyncConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    record_size: int | None = None,
    expect_count: bool = True,
    max_frames: int = 512,
    timeout: float | None = None,
) -> _impl.ResponseFrame:
    """Multi-frame request with sync-equivalent truncation and circuit safety."""

    frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
    if record_size is not None:
        _require_positive_frame_limit(record_size, field="record_size")
    expect_count_enabled = _require_bool_option(expect_count, field="expect_count")
    request_timeout = _require_request_timeout(timeout)
    self._ensure_open()
    await self._acquire_rate()
    slot = await _select_allowed_slot(self)
    if slot is None:
        raise _circuit_unreachable(method)

    conn: _impl.AsyncTcpConnection | None = None
    generation: int | None = None
    started = time.perf_counter()
    first_exc: TdxError | None = None
    cont_exc: TdxError | None = None
    result: _impl.ResponseFrame | None = None

    try:
        conn, generation = await self._acquire_lease(slot)
        try:
            async with conn._lock:
                try:
                    first = await conn._request_locked(method, body, timeout=request_timeout)
                except TdxError as exc:
                    first_exc = exc
                else:
                    payload = first.payload
                    if not expect_count_enabled or len(payload) < 2:
                        result = first
                    else:
                        count = int.from_bytes(payload[:2], "little")
                        chunks = [payload[2:]]
                        got = len(chunks[0])
                        if count > 0 and record_size is None and got > 0:
                            record_size = max(1, got // count)
                        need = count * (record_size or 1) if record_size else None
                        read = 1
                        while need is not None and got < need and read < frame_limit:
                            try:
                                nxt = await conn._read_frame_locked()
                            except TdxError as exc:
                                cont_exc = exc
                                _LOG.warning(
                                    "异步 request_multi 续帧中断（%s: %s），"
                                    "降级返回已合并的 %d 块",
                                    type(exc).__name__,
                                    exc,
                                    len(chunks),
                                )
                                break
                            read += 1
                            if not nxt.payload:
                                break
                            chunks.append(nxt.payload)
                            got += len(nxt.payload)
                        if need is not None and got < need and cont_exc is None:
                            cont_exc = FramingError(
                                "request_multi 响应截断: "
                                f"need={need} got={got} max_frames={frame_limit}",
                                context={
                                    "method": hex(method),
                                    "need": need,
                                    "got": got,
                                    "max_frames": frame_limit,
                                },
                            )
                        joined = b"".join(chunks)
                        result = _impl.ResponseFrame(
                            magic=first.magic,
                            zip_flag=first.zip_flag,
                            seq=first.seq,
                            method=first.method,
                            zip_size=len(joined),
                            unzip_size=len(joined),
                            body=b"",
                            payload=joined,
                            header_raw=first.header_raw,
                        )
        finally:
            await self._release_lease(slot, conn)
    except asyncio.CancelledError:
        await _release_probe_token(self, slot, generation)
        await self._drop(slot, expected=conn)
        raise
    except BaseException:
        await _release_probe_token(self, slot, generation)
        await self._drop(slot, expected=conn)
        raise

    if first_exc is not None:
        await _mark_failure(self, slot, first_exc, generation=generation, conn=conn)
        raise first_exc
    if cont_exc is not None:
        _LOG.warning("异步 request_multi 数据不完整，返回已收到前缀并弃连: %s", cont_exc)
        await _mark_failure(self, slot, cont_exc, generation=generation, conn=conn)
        metrics.record_request(
            command=f"0x{method:04x}",
            ok=False,
            duration=time.perf_counter() - started,
        )
        assert result is not None
        return result

    await _mark_success(
        self,
        slot,
        generation=generation,
        rtt_ms=(time.perf_counter() - started) * 1000.0,
    )
    metrics.record_request(
        command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
    )
    assert result is not None
    return result


async def _iter_frames(
    self: _impl.AsyncConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    max_frames: int = 512,
) -> AsyncIterator[_impl.ResponseFrame]:
    """Frame iterator with sync-equivalent cap validation and dirty-socket safety."""

    frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
    self._ensure_open()
    await self._acquire_rate()
    slot = await _select_allowed_slot(self)
    if slot is None:
        raise _circuit_unreachable(method)

    conn: _impl.AsyncTcpConnection | None = None
    generation: int | None = None
    failed: TdxError | None = None
    frames_read = 0
    try:
        conn, generation = await self._acquire_lease(slot)
        try:
            async with conn._lock:
                frame_bytes, _ = _impl.build_request(
                    method,
                    body,
                    seq=conn.next_seq(),
                    spec=conn.spec,
                )
                writer = conn._writer
                if writer is None:
                    raise ConnectionClosed(
                        f"连接已关闭: {conn.host}:{conn.port}",
                        context={"host": conn.host, "port": conn.port},
                    )
                try:
                    writer.write(frame_bytes)
                    await asyncio.wait_for(writer.drain(), timeout=conn.timeout)
                    conn.stats.bytes_sent += len(frame_bytes)
                except TdxError as exc:
                    failed = exc
                except OSError as exc:
                    failed = ConnectionClosed(
                        f"发送失败: {exc}",
                        context={"host": conn.host, "port": conn.port},
                        cause=exc,
                    )
                if failed is None:
                    while frames_read < frame_limit:
                        try:
                            frame = await conn._read_frame_locked()
                        except TdxError as exc:
                            failed = exc
                            break
                        frames_read += 1
                        conn.stats.last_used = time.time()
                        yield frame
        finally:
            await self._release_lease(slot, conn)
    except asyncio.CancelledError:
        await _release_probe_token(self, slot, generation)
        await self._drop(slot, expected=conn)
        raise
    except BaseException:
        await _release_probe_token(self, slot, generation)
        await self._drop(slot, expected=conn)
        raise

    if failed is not None:
        _LOG.warning("异步 iter_frames 中断（%s: %s），弃连", type(failed).__name__, failed)
        await _mark_failure(self, slot, failed, generation=generation, conn=conn)
        return

    await _mark_success(self, slot, generation=generation)
    # Reaching the caller's cap does not prove the server stream ended. The
    # connection may still contain unread frames from this request, so it must
    # never be returned to the reusable pool in that state.
    if frames_read >= frame_limit or self._closed:
        await self._drop(slot, expected=conn)


async def _heartbeat_loop(self: _impl.AsyncConnectionPool) -> None:
    """Heartbeat mirror that feeds the same circuit/live-health state machine."""

    interval = max(1, int(self.heartbeat_interval or 0))
    while not self._closed:
        await asyncio.sleep(interval)
        for slot in list(self._slots):
            if self._closed:
                return
            if not await _circuit_allows(self, slot.host):
                continue
            conn: _impl.AsyncTcpConnection | None = None
            generation: int | None = None
            try:
                conn, generation = await self._acquire_lease(slot)
                idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                if idle < interval:
                    await self._release_lease(slot, conn)
                    await _release_probe_token(self, slot, generation)
                    continue
                try:
                    rtt = await conn.ping(self.heartbeat_cmd)
                finally:
                    await self._release_lease(slot, conn)
            except asyncio.CancelledError:
                await _release_probe_token(self, slot, generation)
                raise
            except Exception as exc:
                await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            except BaseException:
                await _release_probe_token(self, slot, generation)
                raise
            else:
                await _mark_success(self, slot, generation=generation, rtt_ms=rtt)


# Install onto the canonical public class rather than publishing a second class.
# ``setattr`` keeps this compatibility bridge independent of mypy's method-assign
# diagnostic code names, so --warn-unused-ignores remains meaningful.
_impl.AsyncConnectionPool._circuit_allows = _circuit_allows
_impl.AsyncConnectionPool._select_allowed_slot = _select_allowed_slot
_impl.AsyncConnectionPool._release_probe_token = _release_probe_token
_impl.AsyncConnectionPool._mark_failure = _mark_failure
_impl.AsyncConnectionPool._mark_success = _mark_success
_impl.AsyncConnectionPool.request = _request
_impl.AsyncConnectionPool.request_multi = _request_multi
_impl.AsyncConnectionPool.iter_frames = _iter_frames
_impl.AsyncConnectionPool._heartbeat_loop = _heartbeat_loop
